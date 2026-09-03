"""H ailesi atfı ve terfi kapıları (`mtf_eval_v1`) — SHADOW, `applied=False`.

Bu modül, `multitimeframe_context` tarafından KARAR ANINDA yazılmış bağlamı kapanmış
işlemlerle eşleştirir ve karşı-olgusal ekonomiyi raporlar. Hiçbir şeyi uygulamaz.

**ÖN-H DIŞLAMASI (sözleşme).** Yalnız DEĞİŞMEZ giriş snapshot'ında GERÇEKTEN `mtf_context`
taşıyan adaylar terfi kanıtı sayılır. H yayımlanmadan önce açılmış hiçbir pozisyon — daha
sonra kapansa bile — H kanıtı DEĞİLDİR ve eski snapshot'lara H alanı GERİYE DÖNÜK
YAZILMAZ. F00030 bu nedenle `PRE_H_OBSERVATION_ONLY`dir.

**ABSTAIN üçüncü bir sonuçtur.** Hiçbir yerde `ALLOW` ya da `VETO` sayılmaz; kapsam
(`coverage`) ve çekimserlik oranı (`abstain_rate`) ayrı ayrı raporlanır.

**Maliyet iki kez sayılmaz.** `entry_eval.cost_decomposition` ile aynı ayrım kullanılır:
miras `reported_cost_r` yalnız komisyon+funding, kayma AYRI alandadır.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..core import iso, utc_now
from .entry_eval import (BOOTSTRAP_ALPHA, GATE_MAX_SYMBOL_SHARE, GATE_MIN_DAYS,
                         GATE_MIN_LINKED_CLOSES, GATE_MIN_PER_STRATUM, GATE_STATUS_EVALUATED,
                         GATE_STATUS_LOW_SAMPLE, LINKED, _concentration, _f,
                         _observation_days, _risk_usdt, _s, _stats, bootstrap_ci,
                         cost_decomposition, outcome_id, walk_forward_folds)
from .multitimeframe_context import (ABSTAIN, ALL_PAIRS, ALLOW, PAIR_D_H1, SUPPORTED_PAIRS,
                                     VARIANT_NAMES, VETO, pair_status)

SCHEMA_VERSION = "mtf_eval_v1"

# --- kayıt durumları -----------------------------------------------------------------------
OK = "OK"
NO_SNAPSHOT = "NO_SNAPSHOT"
NO_OUTCOME = "NO_OUTCOME"
PRE_H = "PRE_H_OBSERVATION_ONLY"

#: Kanıt sınıfları. YALNIZ `H_COMPLETE` terfi kapılarına sayılır.
EV_H_COMPLETE = "H_COMPLETE"
EV_PRE_H = "PRE_H_EXCLUDED"
EV_UNLINKED = "UNLINKED_EXCLUDED"

#: Çalışma modları — `entry_eval` ile AYNI fail-closed ilkesi.
MODE_SHADOW = "SHADOW"
MODE_PAPER_BOUNDED = "PAPER_BOUNDED"
MODE_ACTIVE = "ACTIVE"
ALLOWED_MODES = (MODE_SHADOW,)
KNOWN_MODES = (MODE_SHADOW, MODE_PAPER_BOUNDED, MODE_ACTIVE)

#: Bir H bağlamının sonucu GÖRMÜŞ olamayacağının denetlendiği alanlar.
FORBIDDEN_OUTCOME_FIELDS = ("r_multiple", "net_pnl", "pnl", "closed_at", "exit_reason",
                            "won", "outcome_class", "actual_r", "mfe_r", "mae_r")


def has_h_context(snapshot: dict[str, Any] | None) -> bool:
    """Snapshot GERÇEKTEN H bağlamı taşıyor mu. Eksikse ÖN-H'dir; geriye dönük DOLDURULMAZ."""
    ctx = (snapshot or {}).get("mtf_context")
    return isinstance(ctx, dict) and bool(ctx.get("variants"))


def variant_decision(snapshot: dict[str, Any] | None, variant: str) -> dict[str, Any]:
    """Bir varyantın karar anı çıktısı. Yoksa `ABSTAIN` — `ALLOW`a DÜŞÜRÜLMEZ."""
    ctx = (snapshot or {}).get("mtf_context") or {}
    v = (ctx.get("variants") or {}).get(variant)
    if not isinstance(v, dict):
        return {"decision": ABSTAIN, "reason_codes": ["VARIANT_NOT_RECORDED"],
                "pair": None, "variant": variant}
    return v


def leakage_check(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """H bağlamının sonuç alanı taşımadığını ve karar anında yazıldığını doğrular."""
    ctx = (snapshot or {}).get("mtf_context") or {}
    found: list[str] = []
    stages: list[str] = []
    for name, v in (ctx.get("variants") or {}).items():
        if not isinstance(v, dict):
            continue
        stages.append(str(v.get("written_at_stage")))
        if v.get("sees_outcome"):
            found.append(f"{name}:sees_outcome")
        for k in FORBIDDEN_OUTCOME_FIELDS:
            if k in v:
                found.append(f"{name}:{k}")
    return {"clean": not found and all(s == "RANKING" for s in stages) and bool(stages),
            "forbidden_fields_found": sorted(set(found)),
            "stages": sorted(set(stages)), "checked": len(stages)}


# ======================================================================= tek işlem atfı

def evaluate_trade(*, snapshot: dict[str, Any], close: dict[str, Any]) -> dict[str, Any]:
    """Tek kapanmış işlem için bütün H varyantlarının karşı-olgusal sonucu.

    `snapshot` giriş anında yazılmış DEĞİŞMEZ kayıttır; bu fonksiyon onu DEĞİŞTİRMEZ ve
    içine hiçbir sonuç alanı yazmaz.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    cev = _s(close.get("close_event_id")) or ""
    cand = _s(snap.get("candidate_id")) or ""
    r = _f(close.get("r_multiple"))
    pnl = _f(close.get("net_pnl"))
    risk = _risk_usdt(close)
    link = _s(snap.get("link_status")) or LINKED
    h_ok = has_h_context(snap)
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": outcome_id(cev, cand),
        "close_event_id": cev or None,
        "trade_id": _s(close.get("trade_id")),
        "candidate_id": cand or None,
        "decision_id": _s(snap.get("decision_id")),
        "symbol": _s(close.get("symbol")) or _s(snap.get("symbol")),
        "direction": _s(snap.get("direction")) or _s(close.get("side")),
        "regime": _s(snap.get("regime")),
        "opened_at": _s(close.get("opened_at")),
        "closed_at": _s(close.get("closed_at")),
        "exit_reason": _s(close.get("exit_reason")),
        "link_status": link,
        "has_h_context": h_ok,
        "evidence_grade": (EV_H_COMPLETE if (h_ok and link == LINKED)
                           else (EV_UNLINKED if h_ok else EV_PRE_H)),
        "actual_r": r,
        "actual_net_pnl": pnl,
        "initial_risk_usdt": (round(risk, 6) if risk is not None else None),
        "cost_decomposition": cost_decomposition(close, risk),
        "leakage": leakage_check(snap),
        "applied": False,
    }
    if not cand:
        base.update({"status": NO_SNAPSHOT, "variants": {},
                     "note_tr": "Giriş snapshot'ı yok — H atfı ÜRETİLMEDİ."})
        return base
    if not h_ok:
        # H'den ÖNCE açılmış işlem. Sonucu ne olursa olsun TERFİ KANITI SAYILMAZ ve
        # snapshot'a H alanı GERİYE DÖNÜK YAZILMAZ.
        base.update({"status": PRE_H, "variants": {},
                     "note_tr": ("Değişmez giriş snapshot'ı H'den ÖNCEDİR — yalnız gözlem, "
                                 "TERFİ KANITI DEĞİL.")})
        return base
    if r is None:
        base.update({"status": NO_OUTCOME, "variants": {},
                     "note_tr": "Kapanış R'si ölçülemedi — atıf yapılamaz. Sıfır R VARSAYILMAZ."})
        return base

    out: dict[str, Any] = {}
    for name in VARIANT_NAMES:
        v = variant_decision(snap, name)
        d = str(v.get("decision") or ABSTAIN)
        blocked = (d == VETO)
        # `ABSTAIN` engelleme DEĞİLDİR: baseline aynen sürer ve karşı-olgusal R gerçek R'dir.
        cf_r = 0.0 if blocked else r
        cf_pnl = 0.0 if (blocked or pnl is None) else pnl
        out[name] = {
            "decision": d,
            "pair": v.get("pair"),
            "reason_codes": list(v.get("reason_codes") or []),
            "htf_trend": v.get("htf_trend"),
            "htf_interaction": v.get("htf_interaction"),
            "ltf_structure_state": v.get("ltf_structure_state"),
            "ltf_retest_state": v.get("ltf_retest_state"),
            "structural_rr": v.get("structural_rr"),
            "data_quality": {"htf": v.get("htf_data_quality"), "ltf": v.get("ltf_data_quality")},
            "missing_fields": list(v.get("missing_fields") or []),
            "allowed": d == ALLOW,
            "blocked": blocked,
            "abstained": d == ABSTAIN,
            "blocked_loser": bool(blocked and r < 0),
            "blocked_winner": bool(blocked and r > 0),
            "avoided_loss_r": round(-r, 6) if (blocked and r < 0) else 0.0,
            "missed_gain_r": round(r, 6) if (blocked and r > 0) else 0.0,
            "avoided_loss_usdt": (round(-pnl, 6) if (blocked and pnl is not None and pnl < 0)
                                  else 0.0),
            "missed_gain_usdt": (round(pnl, 6) if (blocked and pnl is not None and pnl > 0)
                                 else 0.0),
            "counterfactual_r": round(cf_r, 6),
            "counterfactual_net_pnl": (None if pnl is None else round(cf_pnl, 6)),
            "delta_r": round(cf_r - r, 6),
            "applied": False,
        }
    base.update({"status": OK, "variants": out,
                 "note_tr": "SHADOW karşı-olgusal atıf; aktif giriş kararı DEĞİŞMEDİ."})
    return base


def evaluate_closes(*, closes: Iterable[dict[str, Any]], snapshots: dict[str, dict[str, Any]],
                    links: dict[str, str]) -> list[dict[str, Any]]:
    """Bütün kanonik kapanışları snapshot'larıyla eşleyip kronolojik değerlendirir.

    Aynı `close_event_id` İKİ KEZ sayılmaz: `outcome_id` üzerinden tekilleştirilir.
    """
    rows = sorted((c for c in closes if isinstance(c, dict)),
                  key=lambda c: str(c.get("closed_at") or ""))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in rows:
        tid = _s(c.get("trade_id")) or ""
        cand = links.get(tid)
        snap = snapshots.get(cand) if cand else None
        ev = evaluate_trade(snapshot=snap or {}, close=c)
        if ev["outcome_id"] in seen:
            continue                      # yinelenen kapanış İKİ KEZ SAYILMAZ
        seen.add(ev["outcome_id"])
        out.append(ev)
    return out


# ============================================================== varyant raporu (FAZ 9)

def _variant_report(evs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Tek varyantın karşı-olgusal ekonomisi. `ABSTAIN` ALLOW/VETO'ya KATILMAZ."""
    base_r = [_f(e.get("actual_r")) or 0.0 for e in evs]
    cf_r: list[float] = []
    allow = veto = abstain = 0
    b_loser = b_winner = 0
    avoided_r = missed_r = avoided_usdt = missed_usdt = 0.0
    allowed_r: list[float] = []
    allowed_usdt = 0.0
    reasons: dict[str, int] = {}
    htf_dist: dict[str, int] = {}
    ltf_dist: dict[str, int] = {}
    rrs: list[float] = []
    for e in evs:
        v = (e.get("variants") or {}).get(name) or {}
        r = _f(e.get("actual_r")) or 0.0
        pnl = _f(e.get("actual_net_pnl"))
        cf_r.append(_f(v.get("counterfactual_r")) or 0.0)
        d = str(v.get("decision") or ABSTAIN)
        if d == ALLOW:
            allow += 1
            allowed_r.append(r)
            allowed_usdt += (pnl or 0.0)
        elif d == VETO:
            veto += 1
            b_loser += int(bool(v.get("blocked_loser")))
            b_winner += int(bool(v.get("blocked_winner")))
            avoided_r += _f(v.get("avoided_loss_r")) or 0.0
            missed_r += _f(v.get("missed_gain_r")) or 0.0
            avoided_usdt += _f(v.get("avoided_loss_usdt")) or 0.0
            missed_usdt += _f(v.get("missed_gain_usdt")) or 0.0
        else:
            abstain += 1
        for rc in (v.get("reason_codes") or []):
            reasons[str(rc)] = reasons.get(str(rc), 0) + 1
        for bucket, key in ((htf_dist, "htf_interaction"), (ltf_dist, "ltf_structure_state")):
            k = str(v.get(key) or "UNKNOWN")
            bucket[k] = bucket.get(k, 0) + 1
        rr = _f(v.get("structural_rr"))
        if rr is not None:
            rrs.append(rr)

    n = len(evs)
    decided = allow + veto
    b, c = _stats(base_r), _stats(cf_r)
    deltas = [c_ - b_ for b_, c_ in zip(base_r, cf_r)]
    d_exp = ((c["expectancy_r"] - b["expectancy_r"])
             if (b["expectancy_r"] is not None and c["expectancy_r"] is not None) else None)
    # Maliyet dökümü — bileşenler AYRI, çift sayım YOK.
    fee = fund = slip = tot = 0.0
    n_fee = n_fund = n_slip = n_tot = 0
    for e in evs:
        cd = e.get("cost_decomposition") or {}
        for key, acc in (("fee_drag_r", "fee"), ("funding_drag_r", "fund"),
                         ("slippage_drag_r", "slip"), ("total_measured_friction_r", "tot")):
            val = _f(cd.get(key))
            if val is None:
                continue
            if acc == "fee":
                fee += val; n_fee += 1
            elif acc == "fund":
                fund += val; n_fund += 1
            elif acc == "slip":
                slip += val; n_slip += 1
            else:
                tot += val; n_tot += 1
    return {
        "variant": name,
        "n": n,
        "allow_count": allow, "veto_count": veto, "abstain_count": abstain,
        "n_decided": decided,
        "coverage": (round(decided / n, 6) if n else None),
        "abstain_rate": (round(abstain / n, 6) if n else None),
        "blocked_winners": b_winner, "blocked_losers": b_loser,
        "avoided_loss_r": round(avoided_r, 6), "avoided_loss_usdt": round(avoided_usdt, 6),
        "missed_gain_r": round(missed_r, 6), "missed_gain_usdt": round(missed_usdt, 6),
        "allowed_net_r": (round(sum(allowed_r), 6) if allowed_r else None),
        "allowed_net_usdt": (round(allowed_usdt, 6) if allow else None),
        "allowed_stats": _stats(allowed_r),
        "baseline": b, "counterfactual": c,
        "expectancy_delta_r": (round(d_exp, 6) if d_exp is not None else None),
        "profit_factor": c.get("profit_factor"),
        "max_drawdown_r": c.get("max_drawdown_r"),
        "cvar5_r": c.get("tail_loss_r_cvar5"),
        "delta_ci": bootstrap_ci(deltas) if deltas else {"state": "no_data",
                                                         "excludes_zero": False},
        "reason_distribution": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "htf_interaction_distribution": dict(sorted(htf_dist.items(),
                                                    key=lambda kv: (-kv[1], kv[0]))),
        "ltf_confirmation_distribution": dict(sorted(ltf_dist.items(),
                                                     key=lambda kv: (-kv[1], kv[0]))),
        "structural_rr": ({"n": len(rrs), "mean": round(sum(rrs) / len(rrs), 6),
                           "min": round(min(rrs), 6), "max": round(max(rrs), 6)}
                          if rrs else {"n": 0, "mean": None, "min": None, "max": None}),
        "fee_drag_r": (round(fee, 6) if n_fee else None),
        "funding_drag_r": (round(fund, 6) if n_fund else None),
        "slippage_drag_r": (round(slip, 6) if n_slip else None),
        "total_measured_friction_r": (round(tot, 6) if n_tot else None),
        "cost_measured_counts": {"fee": n_fee, "funding": n_fund, "slippage": n_slip,
                                 "total": n_tot},
        "cost_sensitivity": _cost_sensitivity(evs, name),
        "walk_forward": walk_forward_folds(
            [{"actual_r": e.get("actual_r"), "closed_at": e.get("closed_at"),
              "families": {name: (e.get("variants") or {}).get(name) or {}}} for e in evs],
            name),
        "applied": False,
    }


def _cost_sensitivity(evs: list[dict[str, Any]], name: str,
                      multipliers: tuple[float, ...] = (0.5, 1.0, 2.0)) -> list[dict[str, Any]]:
    """Maliyet çarpanına duyarlılık. Ölçülmemiş maliyet SIFIR SAYILMAZ; ayrıca sayılır."""
    out: list[dict[str, Any]] = []
    unknown = sum(1 for e in evs
                  if _f((e.get("cost_decomposition") or {}).get("total_measured_friction_r"))
                  is None)
    for m in multipliers:
        tot = 0.0
        counted = 0
        for e in evs:
            v = (e.get("variants") or {}).get(name) or {}
            if not v.get("blocked"):
                continue
            c = _f((e.get("cost_decomposition") or {}).get("total_measured_friction_r"))
            if c is None:
                continue
            tot += c * m
            counted += 1
        out.append({"cost_multiplier": m, "avoided_friction_r": round(tot, 6),
                    "n_counted": counted, "n_cost_unmeasured": unknown})
    return out


# ================================================================ TERFİ KAPILARI (FAZ 10)

#: H'ye özgü ek kapılar. Mevcut kapılar GEVŞETİLMEZ.
GATE_MIN_COVERAGE = 0.30
GATE_MAX_ABSTAIN_RATE = 0.70

SAMPLE_DEPENDENT_GATES = (
    "POSITIVE_COST_ADJUSTED_IMPROVEMENT",
    "CONFIDENCE_INTERVAL_EXCLUDES_ZERO",
    "DRAWDOWN_NOT_WORSE",
    "TAIL_RISK_NOT_WORSE",
    "WALK_FORWARD_SIGN_CONSISTENCY",
    "ADEQUATE_COVERAGE",
    "ACCEPTABLE_ABSTAIN_RATE",
)


def gates(rep: dict[str, Any], *, n_h_linked_closes: int, days: float | None,
          dir_counts: dict[str, int], regime_counts: dict[str, int],
          symbol_counts: dict[str, int], leakage: dict[str, Any],
          isolation: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """Tek varyantın terfi kapıları. `None` (ölçülemedi) DAİMA `passed=False` sayılır.

    Örneklem ön koşulu düşerse bağımlı başarım kapıları `NOT_EVALUABLE_LOW_SAMPLE` olur ve
    geçti SAYILMAZ (`entry_eval` 1D düzeltmesiyle AYNI ilke).
    """
    b, c = rep.get("baseline") or {}, rep.get("counterfactual") or {}
    ci = rep.get("delta_ci") or {}
    wf = rep.get("walk_forward") or {}
    dexp = rep.get("expectancy_delta_r")
    cov, ab = rep.get("coverage"), rep.get("abstain_rate")
    dd_b, dd_c = b.get("max_drawdown_r"), c.get("max_drawdown_r")
    cv_b, cv_c = b.get("tail_loss_r_cvar5"), c.get("tail_loss_r_cvar5")
    n = sum(symbol_counts.values()) or 0
    top = (max(symbol_counts.values()) / n) if (n and symbol_counts) else None
    covered_dirs = [k for k, v in dir_counts.items() if v >= GATE_MIN_PER_STRATUM]
    covered_regs = [k for k, v in regime_counts.items() if v >= GATE_MIN_PER_STRATUM]

    def gate(code: str, passed: Any, detail: str) -> dict[str, Any]:
        return {"code": code, "passed": bool(passed), "detail": detail,
                "status": GATE_STATUS_EVALUATED}

    out = [
        gate("MIN_H_LINKED_CLOSES", n_h_linked_closes >= GATE_MIN_LINKED_CLOSES,
             f"{n_h_linked_closes}/{GATE_MIN_LINKED_CLOSES} (yalnız {EV_H_COMPLETE}; "
             f"{EV_PRE_H} SAYILMAZ)"),
        gate("MIN_OBSERVATION_DAYS", days is not None and days >= GATE_MIN_DAYS,
             f"{days}/{GATE_MIN_DAYS}" if days is not None else "ölçülemedi"),
        gate("POINT_IN_TIME_ELIGIBLE", leakage.get("clean"),
             f"{leakage.get('stages')}; denetlenen {leakage.get('checked')}"),
        gate("NO_LEAKAGE", not leakage.get("forbidden_fields_found"),
             f"yasak alan: {leakage.get('forbidden_fields_found') or 'yok'}"),
        gate("DATA_QUALITY_OK", rep.get("n") and not rep.get("data_quality_failures"),
             f"n={rep.get('n')}"),
        gate("ISOLATION_VERIFIED", isolation.get("verified"),
             str(isolation.get("detail") or "doğrulanmadı")),
        gate("SAME_COST_MODEL", rep.get("total_measured_friction_r") is not None,
             f"ölçülen bileşen sayıları {rep.get('cost_measured_counts')}"),
        gate("SHADOW_MODE_ONLY", str(mode).upper() in ALLOWED_MODES,
             f"mode={mode} (izinli: {', '.join(ALLOWED_MODES)})"),
        gate("AUTO_PROMOTION_DISABLED", True, "auto_promotion=false (config ile açılamaz)"),
        gate("ADEQUATE_COVERAGE", cov is not None and cov >= GATE_MIN_COVERAGE,
             f"kapsam {cov} (taban {GATE_MIN_COVERAGE})" if cov is not None else "ölçülemedi"),
        gate("ACCEPTABLE_ABSTAIN_RATE", ab is not None and ab <= GATE_MAX_ABSTAIN_RATE,
             f"çekimser {ab} (tavan {GATE_MAX_ABSTAIN_RATE})" if ab is not None
             else "ölçülemedi"),
        gate("POSITIVE_COST_ADJUSTED_IMPROVEMENT", dexp is not None and dexp > 0.0,
             f"Δbeklenti {dexp}" if dexp is not None else "ölçülemedi"),
        gate("CONFIDENCE_INTERVAL_EXCLUDES_ZERO", ci.get("excludes_zero"),
             f"[{ci.get('lo')}, {ci.get('hi')}] ({ci.get('state')})"),
        gate("DRAWDOWN_NOT_WORSE", dd_b is not None and dd_c is not None and dd_c >= dd_b,
             f"{dd_b} → {dd_c}" if (dd_b is not None and dd_c is not None) else "ölçülemedi"),
        gate("TAIL_RISK_NOT_WORSE", cv_b is not None and cv_c is not None and cv_c >= cv_b,
             f"CVaR5 {cv_b} → {cv_c}" if (cv_b is not None and cv_c is not None)
             else "ölçülemedi"),
        gate("SYMBOL_CONCENTRATION", top is not None and top <= GATE_MAX_SYMBOL_SHARE,
             (f"en yoğun sembol payı {top:.2f} (tavan {GATE_MAX_SYMBOL_SHARE})"
              if top is not None else "ölçülemedi")),
        gate("DIRECTION_COVERAGE", len(covered_dirs) >= 2,
             f"≥{GATE_MIN_PER_STRATUM} kapanışlı yön: {', '.join(covered_dirs) or 'yok'}"),
        gate("REGIME_COVERAGE", len(covered_regs) >= 2,
             f"≥{GATE_MIN_PER_STRATUM} kapanışlı rejim: {', '.join(covered_regs) or 'yok'}"),
        gate("MULTIPLE_SYMBOLS", len(symbol_counts) >= 2,
             f"{len(symbol_counts)} sembol"),
        gate("WALK_FORWARD_SIGN_CONSISTENCY", wf.get("all_folds_positive"),
             (f"{wf.get('n_positive')}/{wf.get('k')} kat pozitif"
              if wf.get("state") == "ok" else "yetersiz örnek — 'bilinmiyor' geçti sayılmaz")),
    ]
    if n_h_linked_closes < GATE_MIN_LINKED_CLOSES:
        for g in out:
            if g["code"] in SAMPLE_DEPENDENT_GATES:
                g["status"] = GATE_STATUS_LOW_SAMPLE
                g["raw_passed"] = g["passed"]
                g["passed"] = False
                g["detail"] = (f"{g['detail']} — {GATE_STATUS_LOW_SAMPLE} "
                               f"({n_h_linked_closes}/{GATE_MIN_LINKED_CLOSES} H-tam kapanış)")
    return out


# ==================================================================== rapor birleştirme

def aggregate(evaluations: Iterable[dict[str, Any]], *, mode: str = MODE_SHADOW,
              isolation: dict[str, Any] | None = None, now=None) -> dict[str, Any]:
    """Varyant bazlı özet + terfi kapıları + dürüstlük bölümü.

    Terfi kanıtı YALNIZ `H_COMPLETE` (H bağlamı taşıyan + gerçek `trade_id` bağı olan)
    kapanışlardan hesaplanır. `PRE_H_EXCLUDED` ayrı bir gözlem bölümünde raporlanır ve
    HİÇBİR kapıya sayılmaz.
    """
    evs = [e for e in evaluations if isinstance(e, dict)]
    ok = [e for e in evs if e.get("status") == OK]
    h_complete = [e for e in ok if e.get("evidence_grade") == EV_H_COMPLETE]
    pre_h = [e for e in evs if e.get("status") == PRE_H]
    no_snap = [e for e in evs if e.get("status") == NO_SNAPSHOT]
    no_out = [e for e in evs if e.get("status") == NO_OUTCOME]
    days = _observation_days(h_complete)
    sym_c = _concentration(h_complete, "symbol")
    dir_c = _concentration(h_complete, "direction")
    reg_c = _concentration(h_complete, "regime")
    leak = {"clean": all((e.get("leakage") or {}).get("clean") for e in h_complete),
            "forbidden_fields_found": sorted({f for e in h_complete
                                              for f in ((e.get("leakage") or {})
                                                        .get("forbidden_fields_found") or [])}),
            "stages": sorted({s for e in h_complete
                              for s in ((e.get("leakage") or {}).get("stages") or [])}),
            "checked": len(h_complete)}
    iso_rep = dict(isolation or {"verified": False, "detail": "ölçüm sağlanmadı"})

    variants: dict[str, Any] = {}
    gates_by_variant: dict[str, Any] = {}
    for name in VARIANT_NAMES:
        rep = _variant_report(h_complete, name)
        rep["observation_only_pre_h"] = {"n": len(pre_h),
                                         "note_tr": "H'den ÖNCE açıldı — kanıt SAYILMAZ."}
        variants[name] = rep
        g = gates(rep, n_h_linked_closes=len(h_complete), days=days, dir_counts=dir_c,
                  regime_counts=reg_c, symbol_counts=sym_c, leakage=leak,
                  isolation=iso_rep, mode=mode)
        gates_by_variant[name] = {
            "gates": g,
            "all_passed": all(x["passed"] for x in g),
            "n_passed": sum(1 for x in g if x["passed"]),
            "n_total": len(g),
            "not_evaluable": [x["code"] for x in g
                              if x["status"] == GATE_STATUS_LOW_SAMPLE],
            "promotion_possible": False,        # otomatik terfi HİÇBİR KOŞULDA mümkün değil
        }

    n_h = len(h_complete)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "mode": str(mode).upper(),
        "applied": False,
        "auto_promotion": False,
        "supported_pairs": list(SUPPORTED_PAIRS),
        "default_pair": PAIR_D_H1,
        # Rapor KENDİ KENDİNE YETER: çift durumları panoya ayrıca hesaplattırılmaz.
        "pair_status": {p: pair_status(p) for p in ALL_PAIRS},
        "n_evaluated": len(evs),
        "n_h_linked_closes": n_h,
        "n_pre_h_excluded": len(pre_h),
        "n_no_snapshot": len(no_snap),
        "n_no_outcome": len(no_out),
        "observation_days": days,
        "symbol_concentration": sym_c,
        "direction_concentration": dir_c,
        "regime_coverage": reg_c,
        "leakage": leak,
        "isolation": iso_rep,
        "variants": variants,
        "promotion_gates": gates_by_variant,
        "state": (("PENDING_FIRST_H_CLOSE" if n_h == 0 else "ACCUMULATING")
                  if n_h < GATE_MIN_LINKED_CLOSES else "SAMPLE_SUFFICIENT"),
        "bootstrap_alpha": BOOTSTRAP_ALPHA,
        "honesty_note_tr": (
            "Kaynak video KÂRLILIK KANITI DEĞİLDİR. H bugüne kadar hiçbir aktif kararı "
            "etkilemedi (`applied=false`). Terfi yalnız manuel operatör onayıyla ve bütün "
            "kapılar geçildikten sonra mümkündür; otomatik terfi hiçbir koşulda yapılmaz. "
            "Örneklem ön koşulu düşerken bağımlı kapılar 'geçti' DEĞİL, "
            f"'{GATE_STATUS_LOW_SAMPLE}' olarak raporlanır."),
    }


def build_report(*, closes: Iterable[dict[str, Any]], snapshots: dict[str, dict[str, Any]],
                 links: dict[str, str], mode: str = MODE_SHADOW,
                 isolation: dict[str, Any] | None = None, now=None) -> dict[str, Any]:
    """Uçtan uca H raporu: kapanışlar → atıf → özet → kapılar."""
    evs = evaluate_closes(closes=closes, snapshots=snapshots, links=links)
    doc = aggregate(evs, mode=mode, isolation=isolation, now=now)
    doc["evaluations"] = evs
    return doc


__all__ = ["SCHEMA_VERSION", "OK", "NO_SNAPSHOT", "NO_OUTCOME", "PRE_H",
           "EV_H_COMPLETE", "EV_PRE_H", "EV_UNLINKED",
           "MODE_SHADOW", "MODE_PAPER_BOUNDED", "MODE_ACTIVE", "ALLOWED_MODES", "KNOWN_MODES",
           "FORBIDDEN_OUTCOME_FIELDS", "GATE_MIN_COVERAGE", "GATE_MAX_ABSTAIN_RATE",
           "SAMPLE_DEPENDENT_GATES", "has_h_context", "variant_decision", "leakage_check",
           "evaluate_trade", "evaluate_closes", "gates", "aggregate", "build_report"]
