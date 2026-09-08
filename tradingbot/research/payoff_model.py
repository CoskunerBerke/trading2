"""Hedefe HİZALI ödeme modeli — koşullu ortalamalar (`research_payoff_v1`).

Mevcut ekonomi `gross = p·avg_win_r − (1−p)·|avg_loss_r|` biçimindedir. Burada iki koşullu
terim de hedefle hizalanır (yalnız kayıp tarafı değil):

    W        = {R > 0.25}                      (üretim etiketiyle AYNI eşik)
    mu_W(t)  = E[R | R > 0.25,  t'den ÖNCE kapanmış kanonik işlemler]
    mu_N(t)  = E[R | R <= 0.25, t'den ÖNCE kapanmış kanonik işlemler]
    EV(t)    = p(t)·mu_W(t) + (1 − p(t))·mu_N(t)

`R` TANIMI (kanonik defterden izlendi, `accounting/futures_ledger.py`):

* payda = **başlangıç riski** = |entry_avg − initial_stop| × **initial_qty**
  (kısmi çıkış paydayı KÜÇÜLTMEZ),
* pay = pozisyonun NET PnL'i — bütün çıkış dolumları toplanır (TP1 kısmi çıkışı DAHİL),
  giriş+çıkış ücretleri ve funding düşülmüş, kayma dolum fiyatının İÇİNDE,
* çıkış politikası = kanonik şampiyon (stop / TP1 %50 / TP2 / başa-baş / likidasyon / zaman).

**R zaten NETTİR** → bu modülde ücret/funding/kayma bir daha DÜŞÜLMEZ.

Olasılık kaynağı her karşılaştırmada SABİT tutulur; ön tahmin ve harman AYRI panellerdir ve
bu, iki olasılıktan hiçbirini DOĞRULAMAZ. Belirsizlik ve yumuşak cezalar DEĞİŞTİRİLMEZ ki
etki ayrıştırılabilsin.
"""
from __future__ import annotations

import math
from typing import Any

from . import CANONICAL_OBSERVED, POINT_IN_TIME, RETROSPECTIVE_RESEARCH
from .dataset import to_ms
from .dual_edge import SCRATCH_R, decompose_edge, recover_prior

SCHEMA_VERSION = "research_payoff_v1"

UNAVAILABLE = "UNAVAILABLE"
PANEL_PRIOR = "FIXED_SOURCE_p_win_prior"
PANEL_BLEND = "FIXED_SOURCE_p_win_blend"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def r_definition() -> dict[str, Any]:
    """`R`'nin kesin sözleşmesi — kaynaktan izlendi, varsayılmadı."""
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": CANONICAL_OBSERVED,
        "source": "accounting/futures_ledger.py (risk = |entry_avg-initial_stop|*initial_qty; "
                  "r_mult = net/risk)",
        "initial_risk_denominator": "|entry_avg − initial_stop| × initial_qty (BAŞLANGIÇ miktarı)",
        "partial_exit_aggregation": ("bütün çıkış dolumları tek `net` içinde toplanır; TP1 kısmi "
                                     "çıkışı dâhildir ve paydayı küçültmez"),
        "costs": ("net = brüt − (giriş+çıkış ücretleri) ∓ funding; kayma dolum fiyatının "
                  "İÇİNDE → R zaten NET, tekrar maliyet düşülmez"),
        "exit_policy": "kanonik şampiyon (stop / TP1 %50 / TP2 / başa-baş / likidasyon / zaman)",
        "target_threshold": f"W = {{R > {SCRATCH_R}}} — üretim etiketiyle aynı (labels.py)",
        "scratch": "SCRATCH (|R| < 0.25) W'nin DIŞINDA, yani N sınıfının içindedir",
    }


def trace_avg_win_r() -> dict[str, Any]:
    """Mevcut `avg_win_r` ampirik koşullu ortalama mı, plan geometrisi mi, harman mı?"""
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": POINT_IN_TIME,
        "source": "opportunity.hierarchical_expectancy",
        "verdict": "BLEND",
        "formula": ("realised_win_r = (exp_r + (1−p_h)·L) / p_h  →  "
                    "avg_win_r = w·realised_win_r + (1−w)·max(0.1, plan.expected_r), "
                    "w = n/(n+BLEND_N=20)"),
        "why_not_an_empirical_conditional_mean": (
            "`exp_r` KOŞULSUZ beklenti tahminidir (bütün kapanışların R ortalaması); "
            "`realised_win_r` bunu p_h ile TERS ÇEVİREREK 'kazanç büyüklüğü' üretir. Bu, "
            "E[R | R > 0.25] DEĞİLDİR ve p_h değişince değeri değişir."),
        "avg_loss_r": ("SABİT −1.0 varsayımı; `hierarchical_expectancy`de `default_loss_r`. "
                       "Ampirik değil."),
        "consequence": ("iki terim de hedefin koşullu ortalaması olmadığı için `gross` bir "
                        "E[R] tahmini olarak okunamaz"),
    }


def past_only_conditional_means(closes: list[dict[str, Any]], as_of_ms: float, *,
                                threshold: float = SCRATCH_R) -> dict[str, Any]:
    """`as_of`tan ÖNCE kapanmış kanonik işlemlerden koşullu ortalamalar.

    Boş sınıf için **uydurma sıfır ÜRETİLMEZ**: `mu` `None`, durum `UNAVAILABLE` olur ve
    gözlem sayısı 0 raporlanır.
    """
    prior_rows = []
    for h in closes:
        c = _f(h.get("closed_at_ms"))
        r = _f(h.get("r_multiple"))
        if c is None or r is None or c >= as_of_ms:
            continue
        prior_rows.append((c, r))
    wins = [r for _, r in prior_rows if r > threshold]
    nots = [r for _, r in prior_rows if r <= threshold]
    latest = max((c for c, _ in prior_rows), default=None)
    return {
        "n_available": len(prior_rows),
        "latest_available_close_ms": latest,
        "n_W": len(wins), "n_N": len(nots),
        "mu_W": (round(sum(wins) / len(wins), 6) if wins else None),
        "mu_N": (round(sum(nots) / len(nots), 6) if nots else None),
        "mu_W_state": ("OK" if wins else UNAVAILABLE),
        "mu_N_state": ("OK" if nots else UNAVAILABLE),
        "threshold": threshold,
        "note": ("yalnız `as_of`tan ÖNCE kapanmış KANONİK nihai işlemler; retrospektif simüle "
                 "sonuç YOKTUR"),
    }


def minus_one_approximation_check(closes: list[dict[str, Any]], *,
                                  threshold: float = SCRATCH_R) -> dict[str, Any]:
    """`avg_loss_r = −1.0` yaklaşımının YÖNÜ — bu kohorttan ölçülür, varsayılmaz."""
    rs = [r for r in (_f(h.get("r_multiple")) for h in closes) if r is not None]
    nots = [r for r in rs if r <= threshold]
    scratch = [r for r in rs if abs(r) < threshold]
    mu_n = (sum(nots) / len(nots)) if nots else None
    direction = UNAVAILABLE
    if mu_n is not None:
        if mu_n < -1.0:
            direction = "UNDERSTATES_LOSS(-1R gerçek kaybı OLDUĞUNDAN KÜÇÜK gösterir)"
        elif mu_n > -1.0:
            direction = "OVERSTATES_LOSS(-1R gerçek kaybı OLDUĞUNDAN BÜYÜK gösterir)"
        else:
            direction = "EXACT"
    return {
        "n_closes": len(rs), "n_not_win": len(nots), "n_scratch": len(scratch),
        "mean_R_given_not_win": (round(mu_n, 6) if mu_n is not None else None),
        "assumed_avg_loss_r": -1.0,
        "difference": (round(mu_n + 1.0, 6) if mu_n is not None else None),
        "direction": direction,
        "warning": ("SCRATCH varlığı yönü TERSİNE çevirebilir; sabit-72sa kohortunun SCRATCH'leri "
                    "bu kanonik tahmine TAŞINMAZ"),
        "evidence_class": CANONICAL_OBSERVED,
    }


def payoff_comparison(opps: list[dict[str, Any]], closes: list[dict[str, Any]], *,
                      panel: str = PANEL_PRIOR) -> dict[str, Any]:
    """Eski ödeme semantiği ile hedefe hizalı koşullu ortalamaları karşılaştırır.

    Olasılık kaynağı panel boyunca SABİTtir. Belirsizlik ve yumuşak cezalar DEĞİŞMEZ.
    Aday sırası kararlıdır (`as_of`, sonra `candidate_id`).
    """
    ordered = sorted([o for o in opps if o.get("ts_ms") is not None],
                     key=lambda o: (float(o["ts_ms"]), str(o.get("candidate_id") or "")))
    rows: list[dict[str, Any]] = []
    unavailable = {"no_probability": 0, "no_payoff": 0, "mu_W": 0, "mu_N": 0, "no_decomp": 0}
    for o in ordered:
        cid = str(o.get("candidate_id") or "")
        ts = float(o["ts_ms"])
        dec = decompose_edge(o)
        if dec.get("state") != "OK":
            unavailable["no_decomp"] += 1
            continue
        if panel == PANEL_PRIOR:
            p = _f(o.get("p_win_prior"))
            prov = "recorded"
            if p is None:
                rec = recover_prior(o)
                if rec.get("p_gate") is not None and rec.get("in_unit_interval"):
                    p, prov = rec["p_gate"], "DERIVED"
        else:
            p, prov = _f(o.get("p_win_model")), "recorded"
        if p is None:
            unavailable["no_probability"] += 1
            continue
        w_old, l_old = _f(o.get("avg_win_r")), _f(o.get("avg_loss_r"))
        if w_old is None or l_old is None:
            unavailable["no_payoff"] += 1
            continue
        cm = past_only_conditional_means(closes, ts)
        cost_r = dec["cost_r"] or 0.0
        unc = dec["uncertainty_r"] or 0.0
        soft = dec["soft_penalty_r"] or 0.0
        gross_old = p * abs(w_old) - (1.0 - p) * abs(l_old)
        cons_old = gross_old - cost_r - unc - soft
        row = {
            "candidate_id": cid, "decision_id": o.get("decision_id"), "as_of": o.get("ts"),
            "symbol": o.get("symbol"), "linked_trade_id": o.get("linked_trade_id"),
            "panel": panel, "p": round(p, 6), "p_provenance": prov,
            "avg_win_r_old": w_old, "avg_loss_r_old": l_old,
            "gross_old": round(gross_old, 6), "conservative_old": round(cons_old, 6),
            "tradeable_old": (None if dec.get("gates_blocked") is None and cons_old > 0
                              else bool(cons_old > 0 and not dec.get("gates_blocked"))),
            "cost_r": cost_r, "uncertainty_penalty_r": unc, "soft_penalty_r": soft,
            "n_available_closes": cm["n_available"], "n_W": cm["n_W"], "n_N": cm["n_N"],
            "mu_W": cm["mu_W"], "mu_N": cm["mu_N"],
            "mu_W_state": cm["mu_W_state"], "mu_N_state": cm["mu_N_state"],
            "latest_available_close_ms": cm["latest_available_close_ms"],
            "evidence_class": RETROSPECTIVE_RESEARCH,
        }
        if cm["mu_W"] is None or cm["mu_N"] is None:
            unavailable["mu_W" if cm["mu_W"] is None else "mu_N"] += 1
            row.update({"gross_new": None, "conservative_new": None, "tradeable_new": None,
                        "unavailable_reason": ("mu_W yok" if cm["mu_W"] is None else "mu_N yok")})
            rows.append(row)
            continue
        gross_new = p * cm["mu_W"] + (1.0 - p) * cm["mu_N"]
        cons_new = gross_new - cost_r - unc - soft
        row.update({
            "gross_new": round(gross_new, 6), "conservative_new": round(cons_new, 6),
            "delta_gross": round(gross_new - gross_old, 6),
            "delta_conservative": round(cons_new - cons_old, 6),
            "tradeable_new": (None if dec.get("gates_blocked") is None and cons_new > 0
                              else bool(cons_new > 0 and not dec.get("gates_blocked"))),
        })
        rows.append(row)

    both = [r for r in rows if r.get("tradeable_old") is not None
            and r.get("tradeable_new") is not None]
    tt = sum(1 for r in both if r["tradeable_old"] and r["tradeable_new"])
    tf = sum(1 for r in both if r["tradeable_old"] and not r["tradeable_new"])
    ft = sum(1 for r in both if not r["tradeable_old"] and r["tradeable_new"])
    ff = sum(1 for r in both if not r["tradeable_old"] and not r["tradeable_new"])
    dg = [r["delta_gross"] for r in rows if r.get("delta_gross") is not None]
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": RETROSPECTIVE_RESEARCH,
        "panel": panel,
        "probability_source_fixed": True,
        "penalties_unchanged": True,
        "n_opportunities": len(ordered), "n_rows": len(rows),
        "n_with_new_estimate": len(dg), "unavailable": unavailable,
        "coverage_fraction": (round(len(dg) / len(ordered), 4) if ordered else None),
        "mean_delta_gross": (round(sum(dg) / len(dg), 6) if dg else None),
        "max_abs_delta_gross": (round(max((abs(x) for x in dg), default=0.0), 6) if dg else None),
        "gate_disagreement": {"old_YES_new_YES": tt, "old_YES_new_NO": tf,
                              "old_NO_new_YES": ft, "old_NO_new_NO": ff,
                              "n_comparable": len(both)},
        "rows": rows,
        "caveats": [
            "EV'nin BÜYÜMESİ ya da red oranının düşmesi kârlılık SONUCU DEĞİLDİR.",
            "Bu panel olasılık kaynağını SABİT tutar; hiçbir olasılığı doğrulamaz.",
            "mu_W/mu_N yalnız `as_of`tan önce kapanmış KANONİK işlemlerden; erken adaylarda "
            "sınıf boş olabilir ve tahmin UNAVAILABLE kalır (sıfır uydurulmaz).",
            "Belirsizlik ve yumuşak cezalar DEĞİŞTİRİLMEDİ; fark yalnız ödeme teriminden gelir.",
        ],
    }


def accounting_reconciliation(closes: list[dict[str, Any]]) -> dict[str, Any]:
    """Net/brüt mutabakatı — R'nin net olduğunu ve maliyetin iki kez sayılmadığını gösterir."""
    net = sum((_f(h.get("net_pnl")) or 0.0) for h in closes)
    gross = sum((_f(h.get("gross_pnl")) or 0.0) for h in closes)
    fees = sum((_f(h.get("fees")) or 0.0) for h in closes)
    fund = sum((_f(h.get("funding")) or 0.0) for h in closes)
    resid = net - (gross - fees + fund)
    return {
        "n": len(closes), "sum_net_pnl": round(net, 10), "sum_gross_pnl": round(gross, 10),
        "sum_fees": round(fees, 10), "sum_funding": round(fund, 10),
        "identity": "net = gross − fees + funding",
        "residual": round(resid, 10), "reconciled": bool(abs(resid) <= 1e-8),
        "implication": ("R = net / başlangıç riski → ödeme modelinde maliyet TEKRAR düşülmez"),
        "evidence_class": CANONICAL_OBSERVED,
    }


__all__ = ["PANEL_BLEND", "PANEL_PRIOR", "SCHEMA_VERSION", "UNAVAILABLE",
           "accounting_reconciliation", "minus_one_approximation_check",
           "past_only_conditional_means", "payoff_comparison", "r_definition",
           "trace_avg_win_r"]
