"""Eşleşmiş çift-kenar (dual-edge) karşılaştırması ve olasılık sözleşmeleri
(`research_dual_edge_v1`).

Bu modül **hiçbir üretim davranışını değiştirmez**: karar hattını yeniden sıralamaz, olasılık
kaynağını değiştirmez, kapı/eşik/risk dokunmaz. Yaptığı tek şey, ZATEN YAZILMIŞ karar anı
snapshot'ları üzerinde aynı ekonomik formülü **yalnız olasılık kaynağını değiştirerek** ikinci kez
hesaplamak ve iki kapı kararını yan yana raporlamaktır.

Sözleşmeler:
* Geometri, ödeme (`avg_win_r`/`avg_loss_r`), maliyet (`cost_r`), belirsizlik cezası, yumuşak
  ceza ve `as_of` **SABİT** tutulur. Değişen tek girdi olasılıktır.
* Eksik girdi **eksik kalır**: sıfıra çevrilmez, otomatik veto sayılmaz.
* Tarihsel bir model tahmini yoksa BUGÜNKÜ model çalıştırılıp geçmişe yazılmaz.
* Hedef (target) ve ufuk uyumsuzsa ekonomik ikame YAPILMAZ; satır `INCOMPATIBLE_TARGET` olur.
* Ekonomik kapı verdikti ile tam portföy kararı (kaldıraç kapısı, risk kapasitesi, chief)
  AYRI raporlanır.
"""
from __future__ import annotations

import math
import re
from typing import Any

from . import MISSING_OR_UNMEASURABLE, POINT_IN_TIME, RETROSPECTIVE_RESEARCH

SCHEMA_VERSION = "research_dual_edge_v1"

#: `opportunity.assess` formül kimliği — bu satırlar bu sürümle yeniden hesaplandı.
FORMULA_ID = "opportunity.assess/v1(gross=p*W-(1-p)*|L|; net=gross-cost_r; " \
             "conservative=net-uncertainty-soft)"

#: `opportunity.py` sabitleri (kaynakla birebir; değişirse test düşer).
UNCERTAINTY_K = 0.20
MIN_TRADE_MULTIPLIER = 0.20
RESEARCH_MULTIPLIER = 0.25
SOFT_CAP = 0.60

#: Dondurulmuş etiket kuralı — `learn/labels.py::label_outcome` ile AYNI eşiği kullanır.
LABEL_RULE_ID = "label_v1_scratch_excluded(|r|<0.25 -> SCRATCH; r>0 -> WIN)"
SCRATCH_R = 0.25

WIN, LOSS, SCRATCH, PENDING = "WIN", "LOSS", "SCRATCH", "PENDING_CENSORED"

COMPATIBLE = "COMPATIBLE"
PARTIAL_COMPATIBLE = "PARTIAL_COMPATIBLE"
INCOMPATIBLE = "INCOMPATIBLE_TARGET"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------- 1) kaynak izi
def _enclosing_def(src_lines: list[str], line_no: int | None) -> str | None:
    """Verilen satırı içeren `def`in adı — çağrı sırası ile TANIM sırası karıştırılmasın diye."""
    if not line_no:
        return None
    for i in range(line_no - 1, -1, -1):
        m = re.match(r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)", src_lines[i])
        if m:
            return m.group(2)
    return None


def pipeline_trace(engine_src: str, head_src: str) -> dict[str, Any]:
    """Karar hattının SIRASI kaynaktan ölçülür — iddia sabitlenmez.

    Altı çapa aranır: ön tahmin üretimi → ekonomik hesap ÇAĞRISI → sıralama → model tahmini
    ataması → giriş sırası → kabul. Sıra karşılaştırması YALNIZ aynı kapsayıcı fonksiyon
    içindeki satırlar için yapılır: bir fonksiyonun GÖVDESİ, çağrıldığı satırdan sonra
    görünse bile o çağrıdan sonra çalışmaz.
    """
    def _find_all(src_lines: list[str], pattern: str) -> list[int]:
        rx = re.compile(pattern)
        return [i for i, ln in enumerate(src_lines, start=1) if rx.search(ln)]

    def _first(src_lines: list[str], pattern: str) -> int | None:
        hits = _find_all(src_lines, pattern)
        return hits[0] if hits else None

    e_lines = engine_src.splitlines()
    h_lines = head_src.splitlines()
    anchors = {
        "prior_creation": {"file": "coinhead/head.py",
                           "line": _first(h_lines, r"^\s*d\.p_win\s*=\s*round\(0\.5\s*\+"),
                           "what": "d.p_win = 0.5 + 0.25*conf*[|score|>=threshold]"},
        "economic_calc_call": {"file": "engine_v3.py",
                               "line": _first(e_lines, r"^\s*self\._assess_opportunities\("),
                               "what": "_assess_opportunities ÇAĞRISI -> opportunity.assess"},
        "chief_ranking": {"file": "engine_v3.py",
                          "line": _first(e_lines, r"^\s*chief\s*=\s*self\.chief_mgr\.decide\("),
                          "what": "chief.decide -> conservative_net_edge_r ile sıralama"},
        "model_assign": {"file": "engine_v3.py",
                         "line": _first(e_lines, r"^\s*d\.p_win\s*=\s*b\.p_win\s*$"),
                         "what": "d.p_win = b.p_win (learner harmanı + influence)"},
        "entry_order": {"file": "engine_v3.py",
                        "line": _first(e_lines, r"_order\.sort\(key=lambda s:"),
                        "what": "giriş döngüsü sırası — AYNI conservative_net_edge_r"},
        "admission": {"file": "engine_v3.py",
                      "line": _first(e_lines, r'_opp\.get\("tradeable"\)'),
                      "what": "kabul: opportunity.tradeable"},
    }
    for a in anchors.values():
        src = h_lines if a["file"].endswith("head.py") else e_lines
        a["enclosing_def"] = _enclosing_def(src, a["line"])

    assess_calls = _find_all(e_lines, r"^\s*self\._assess_opportunities\(")
    opp_assign = [{"line": i, "enclosing_def": _enclosing_def(e_lines, i)}
                  for i in _find_all(e_lines, r"^\s*d\.opportunity\s*=")]
    ec, ma = anchors["economic_calc_call"], anchors["model_assign"]
    same_scope = bool(ec["line"] and ma["line"]
                      and ec["enclosing_def"] == ma["enclosing_def"])
    gate_before_model = bool(same_scope and ec["line"] < ma["line"])
    # GERÇEK yeniden hesap: model atamasından SONRA gelen bir ÇAĞRI (tanım gövdesi değil).
    recompute_calls = [i for i in assess_calls if ma["line"] and i > ma["line"]
                       and _enclosing_def(e_lines, i) == ma["enclosing_def"]]
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": POINT_IN_TIME,
        "anchors": anchors,
        "all_anchors_found": all(a["line"] is not None for a in anchors.values()),
        "same_enclosing_scope": same_scope,
        "enclosing_scope": ec.get("enclosing_def"),
        "economic_gate_before_model_assignment": gate_before_model,
        "assess_call_sites": assess_calls,
        "opportunity_assignments": opp_assign,
        "recompute_call_sites_after_model": recompute_calls,
        "alternate_recompute_path": bool(recompute_calls),
        "method_note": ("`d.opportunity` ataması `_assess_opportunities` TANIMI içindedir "
                        "(satır no daha büyük olabilir); yeniden hesap ancak model atamasından "
                        "SONRA GELEN bir ÇAĞRI ile olur — bu yüzden çağrı yerleri sayılır"),
        "note": ("SIRA tek başına 'istenmeyen davranış' kanıtı DEĞİLDİR; sınıflandırma için "
                 "yapılandırılmış öğrenme modu ve raporlanan alanlar da gerekir"),
    }


def classify_separation(trace: dict[str, Any], *, influence_mode: str | None,
                        champion_model_present: bool, calibrator_fitted: bool,
                        snapshot_reports_model_p_win: bool) -> dict[str, Any]:
    """Ayrımı KANITLA sınıflandır: kasıtlı SHADOW mu, tutarsız raporlama mı, eksik entegrasyon mu,
    yoksa uygulama kusuru mu?

    Kanıt kuralları:
    * Ekonomik kapı model olasılığından ÖNCE çalışıyor **ve** sonradan yeniden hesap YOK →
      öğrenilmiş olasılık kapıya hiçbir yoldan ulaşmıyor.
    * Snapshot yine de `p_win` alanına model çıktısını yazıyorsa, kayıt kapının kullandığı
      girdiyi YANSITMIYOR → en azından **tutarsız raporlama**.
    * Öğrenme modu OFF/SHADOW ise ayrım tasarım gereği olabilir; PAPER_BOUNDED ise etkinin
      p_win üzerinden geçmesi beklenirdi → **eksik entegrasyon**.
    """
    gate_first = trace.get("economic_gate_before_model_assignment")
    if gate_first is None:
        return {"classification": "UNVERIFIABLE", "reason": "kaynak çapaları bulunamadı"}
    reasons: list[str] = []
    if not gate_first:
        return {"classification": "NO_SEPARATION",
                "reason": "ekonomik kapı model atamasından SONRA çalışıyor"}
    if trace.get("alternate_recompute_path"):
        return {"classification": "RECOMPUTED_LATER",
                "reason": "model atamasından sonra d.opportunity yeniden yazılıyor",
                "lines": trace.get("recomputed_after_model_assignment")}
    reasons.append("ekonomik kapı model atamasından ÖNCE çalışıyor ve sonradan yeniden "
                   "hesaplanmıyor")
    mode = (influence_mode or "").upper()
    if mode in ("OFF", "SHADOW"):
        cls = "INTENTIONAL_SHADOW_PLUS_INCONSISTENT_REPORTING" if snapshot_reports_model_p_win \
            else "INTENTIONAL_SHADOW"
        reasons.append(f"öğrenme etkisi modu {mode} — baseline davranış tasarım gereği korunur")
    else:
        cls = "MISSING_INTEGRATION"
        reasons.append(f"öğrenme etkisi modu {mode}: etki p_win üzerinden uygulanıyor, fakat "
                       "p_win ekonomik kapıya hiçbir yoldan ulaşmıyor")
    if snapshot_reports_model_p_win:
        reasons.append("snapshot `p_win` alanına kapının KULLANMADIĞI değeri yazıyor — "
                       "kayıt, kapının girdisini yansıtmıyor (tutarsız raporlama)")
    if not champion_model_present:
        reasons.append("champion `p_win_lr` modeli YOK → snapshot `p_win`i fit edilmiş bir "
                       "model çıktısı değil, hiyerarşik önsel + legacy tahmin harmanı")
    if not calibrator_fitted:
        reasons.append("Platt kalibratörü fit EDİLMEMİŞ (n_fit=0) → 'kalibre' nitelemesi "
                       "DESTEKLENMEZ")
    return {"classification": cls, "reasons": reasons,
            "influence_mode": influence_mode,
            "champion_model_present": champion_model_present,
            "calibrator_fitted": calibrator_fitted,
            "evidence_class": POINT_IN_TIME}


# ------------------------------------------------- 2) olasılık sözleşmeleri (hedef + ufuk)
def probability_contracts(*, champion_model_present: bool, calibrator: dict[str, Any] | None,
                          n_closed: int | None, learn_updated_at: str | None) -> dict[str, Any]:
    """Her olasılığın TAM OLARAK neyi ve hangi ufukta öngördüğü.

    Hedef-hattı uyumu bu tabloya göre belirlenir; farklı hedefler karşılaştırılmaz.
    """
    cal = calibrator or {}
    n_fit = int(cal.get("n_fit") or 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "p_win_prior": {
            "source": "coinhead/head.py — d.p_win = 0.5 + 0.25*consensus_conf*[|score|>=eşik]",
            "recorded_as": "entry_snapshot.features.p_win_prior (learn/snapshot.py <- plan.p_win)",
            "predicted_event": "TANIMSIZ — kalibre bir olasılık değil, konsensüs kanaatinin "
                               "monoton dönüşümü",
            "horizon": "TANIMSIZ",
            "range": [0.5, 0.75],
            "training_cutoff": "YOK (fit edilmez)",
            "artifact_identity": "kod sabiti; model artifact'i yok",
            "is_fitted_model": False,
            "is_calibrated": False,
        },
        "p_win_model": {
            "source": ("engine_v3 -> LearnerV2.predict; champion `p_win_lr` YOKSA "
                       "baseline = 0.5*hiyerarşik_önsel + 0.5*legacy_v1_tahmin, "
                       "ardından PAPER_BOUNDED etkisi (<= influence_max_fraction)"),
            "recorded_as": "entry_snapshot.p_win / decision_journal.p_win",
            "predicted_event": ("P(kapanışta r_multiple > +0.25R) — `learn/labels.py::label_outcome` "
                                "SCRATCH eşiği dışlanmış KAZANÇ"),
            "horizon": ("işlem ÖMRÜ (değişken): stop / hedef / başa-baş / zaman çıkışı — "
                        "SABİT ufuk DEĞİL, hedef-vuruş olasılığı DEĞİL"),
            "training_cutoff": learn_updated_at,
            "n_closed_trained_on": n_closed,
            "artifact_identity": ("champion model YOK" if not champion_model_present
                                  else "p_win_lr champion"),
            "is_fitted_model": bool(champion_model_present),
            "is_calibrated": bool(n_fit > 0),
            "calibrator_n_fit": n_fit,
        },
        "payoff_model": {
            "source": "opportunity.hierarchical_expectancy (LearnerV2.exp_r + plan geometrisi harmanı)",
            "recorded_as": "entry_snapshot.avg_win_r / avg_loss_r",
            "predicted_event": "E[R | kazanç] ve E[R | kayıp]; kayıp tarafı SABİT -1.0 varsayımı",
            "horizon": "işlem ömrü (p_win_model ile AYNI)",
            "caveat": ("`avg_loss_r = -1.0` ölçülmüş değil VARSAYIMdır; kanonik ölçülen ortalama "
                       "kayıp -1.0323R"),
        },
        "target_compatibility": {
            "prior_vs_payoff": INCOMPATIBLE,
            "prior_vs_payoff_reason": ("ön tahminin öngördüğü olay tanımsız; ödeme modeli "
                                       "'kazanç' olayını R>0 olarak kullanıyor"),
            "model_vs_payoff": PARTIAL_COMPATIBLE,
            "model_vs_payoff_reason": ("aynı kaynak (kapanmış PAPER işlemleri) ve aynı ufuk "
                                       "(işlem ömrü); TEK sapma: model etiketi SCRATCH'i "
                                       "(|r|<0.25R) kazanç saymaz, ödeme modeli ise kazancı "
                                       "R>0 kabul eder"),
        },
    }


# ------------------------------------------------------------- 3) tek paydalı kullanılabilirlik
def availability_table(opps: list[dict[str, Any]]) -> dict[str, Any]:
    """TEK payda: bütün fırsatlar. Paydalar KARIŞTIRILMAZ.

    Raporun daha önceki "256/256 ve 0/404" ifadesi iki AYRI alt kümeyi gösteriyordu; burada
    her sayaç aynı 404'lük evren üzerinden verilir.
    """
    total = len(opps)
    have_prior = have_model = have_payoff = have_gross = have_edge = 0
    prior_and_model = testable_prior = testable_model = 0
    reasons: dict[str, int] = {}
    for o in opps:
        p_pr, p_md = o.get("p_win_prior"), o.get("p_win_model")
        w, ell = o.get("avg_win_r"), o.get("avg_loss_r")
        g, e = o.get("gross_expectancy_r"), o.get("conservative_net_edge_r")
        payoff = w is not None and ell is not None
        have_prior += p_pr is not None
        have_model += p_md is not None
        have_payoff += payoff
        have_gross += g is not None
        have_edge += e is not None
        prior_and_model += (p_pr is not None and p_md is not None)
        testable_prior += (p_pr is not None and payoff and g is not None)
        testable_model += (p_md is not None and payoff and g is not None)
        if p_pr is None:
            key = "prior_missing:" + ("no_features" if not o.get("decision_close")
                                      else "feature_absent")
            reasons[key] = reasons.get(key, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION, "denominator": total,
        "counts": {
            "p_win_prior_available": have_prior,
            "p_win_model_available": have_model,
            "payoff_available": have_payoff,
            "gross_expectancy_available": have_gross,
            "conservative_edge_available": have_edge,
            "prior_and_model_both_available": prior_and_model,
            "identity_testable_with_prior": testable_prior,
            "identity_testable_with_model": testable_model,
        },
        "missing": {
            "p_win_prior_missing": total - have_prior,
            "p_win_model_missing": total - have_model,
            "payoff_missing": total - have_payoff,
        },
        "exclusion_reasons": reasons,
        "note": ("önceki raporun 256/256 ve 0/404 ifadeleri FARKLI paydalardı: 256 = ön tahmini "
                 "olan alt küme, 404 = tüm fırsatlar. Bu tabloda payda her satırda aynıdır."),
    }


# --------------------------------------------------------------------- 4) kenar ayrıştırma
def recover_prior(opp: dict[str, Any]) -> dict[str, Any]:
    """Ekonomik kapının KULLANDIĞI olasılığı kayıtlı alanlardan CEBIRSEL olarak geri çıkarır.

    `gross = p·W − (1−p)·|L|`  →  `p = (gross + |L|) / (W + |L|)`.

    Neden gerekli: `p_win_prior` yalnız `features` bloğu doluyken kayıtlıdır (NO_TRIGGER
    satırlarında blok boştur), ama `gross_expectancy_r`, `avg_win_r` ve `avg_loss_r` HER
    satırda vardır. Bu ters çevirme yeni bir varsayım getirmez; aynı kimliğin çözümüdür.
    Doğruluğu, ön tahminin doğrudan kayıtlı olduğu satırlarda ÖLÇÜLÜR.
    """
    g, w, ell = _f(opp.get("gross_expectancy_r")), _f(opp.get("avg_win_r")),         _f(opp.get("avg_loss_r"))
    if g is None or w is None or ell is None:
        return {"state": MISSING_OR_UNMEASURABLE, "p_gate": None}
    denom = abs(w) + abs(ell)
    if denom <= 0:
        return {"state": "DEGENERATE_PAYOFF", "p_gate": None}
    p = (g + abs(ell)) / denom
    return {"state": "OK", "p_gate": round(p, 6),
            "method": "gross kimliğinin tersi (p = (gross+|L|)/(W+|L|))",
            "in_unit_interval": bool(-1e-9 <= p <= 1.0 + 1e-9)}


def prior_recovery_check(opps: list[dict[str, Any]]) -> dict[str, Any]:
    """Ters çevirmenin doğruluğu — ön tahminin DOĞRUDAN kayıtlı olduğu satırlarda ölçülür."""
    errs, n_ok, n_out = [], 0, 0
    for o in opps:
        rec = recover_prior(o)
        if rec.get("p_gate") is None:
            continue
        if not rec["in_unit_interval"]:
            n_out += 1
        p_pr = _f(o.get("p_win_prior"))
        if p_pr is None:
            continue
        n_ok += 1
        errs.append(abs(rec["p_gate"] - p_pr))
    return {"n_validated": n_ok, "n_outside_unit_interval": n_out,
            "max_abs_error": (round(max(errs), 8) if errs else None),
            "mean_abs_error": (round(sum(errs) / len(errs), 8) if errs else None),
            "note": ("sapma yalnız kayıttaki 6 ondalık yuvarlamadan gelir; ters çevirme "
                     "yeni bir model ya da varsayım DEĞİLDİR")}


def decompose_edge(opp: dict[str, Any]) -> dict[str, Any]:
    """Kayıtlı kenarı bileşenlerine ayırır: cost_r, belirsizlik cezası, yumuşak ceza.

    Bu ayrıştırma olmadan `gross_expectancy_r` ile `conservative_net_edge_r` KARIŞTIRILIR —
    ilki basit ağırlıklı beklenti, ikincisi iki kesintiden SONRAki değerdir.
    """
    g = _f(opp.get("gross_expectancy_r"))
    n = _f(opp.get("net_expectancy_r"))
    u = _f(opp.get("uncertainty_penalty_r"))
    c = _f(opp.get("conservative_net_edge_r"))
    mult = _f(opp.get("size_multiplier"))
    if None in (g, n, u, c):
        return {"state": MISSING_OR_UNMEASURABLE, "cost_r": None, "uncertainty_r": u,
                "soft_penalty_r": None}
    cost_r = round(g - n, 9)
    soft = round(n - u - c, 9)
    # `gates.blocked` ancak mult==0 VE net>0 iken KESİN bilinir (aksi hâlde iki yol da 0 verir).
    if mult is None:
        blocked = None
    elif mult > 0:
        blocked = False
    elif n > 0:
        blocked = True
    else:
        blocked = None
    return {"state": "OK", "cost_r": cost_r, "uncertainty_r": u, "soft_penalty_r": soft,
            "soft_within_cap": bool(soft <= SOFT_CAP + 1e-9),
            "gates_blocked": blocked,
            "identity_holds": bool(abs((n - u - soft) - c) <= 1e-6),
            "note": ("cost_r, expectancy_basis=NET_OUTCOME iken 0'dır (çift sayım koruması); "
                     "soft ceza GateLedger toplamıdır ve 0.60 ile sınırlıdır")}


def _tradeable(edge: float | None, blocked: bool | None) -> bool | None:
    """`opportunity.assess` kabul mantığının BİREBİR yeniden ifadesi.

    `tradeable = (not blocked) and conservative > 0 and size_multiplier > 0`.
    Kenar <= 0 ise sonuç bloklamadan BAĞIMSIZ olarak False'tur (kesin). Kenar > 0 ama bloklama
    durumu bilinmiyorsa sonuç **bilinmiyor** kalır — varsayılmaz.
    """
    if edge is None:
        return None
    if edge <= 0:
        return False
    if blocked is None:
        return None
    return not blocked


def _recompute(p: float, w: float, ell: float, *, cost_r: float, unc: float,
               soft: float) -> dict[str, float]:
    gross = p * abs(w) - (1.0 - p) * abs(ell)
    net = gross - cost_r
    return {"gross_expectancy_r": round(gross, 6), "net_expectancy_r": round(net, 6),
            "conservative_net_edge_r": round(net - unc - soft, 6)}


# --------------------------------------------------------------------- 5) çift kapı satırları
def dual_edge_rows(opps: list[dict[str, Any]], *, links: dict[str, str],
                   canonical_by_trade: dict[str, dict[str, Any]],
                   compatibility: str = PARTIAL_COMPATIBLE) -> list[dict[str, Any]]:
    """Her kullanılabilir aday için ön tahmin ve model kenarını YAN YANA üretir.

    Değişen tek girdi olasılıktır; geometri/ödeme/maliyet/ceza/`as_of` sabittir.
    """
    rows: list[dict[str, Any]] = []
    for o in opps:
        cand = str(o.get("candidate_id") or "")
        tid = links.get(cand)
        can = canonical_by_trade.get(tid or "", {})
        dec = decompose_edge(o)
        p_pr, p_md = _f(o.get("p_win_prior")), _f(o.get("p_win_model"))
        rec = recover_prior(o)
        p_gate, p_gate_src = p_pr, "recorded:features.p_win_prior"
        if p_gate is None and rec.get("p_gate") is not None and rec.get("in_unit_interval"):
            p_gate, p_gate_src = rec["p_gate"], "recovered:gross-identity-inversion"
        w, ell = _f(o.get("avg_win_r")), _f(o.get("avg_loss_r"))
        base = {
            "candidate_id": cand, "decision_id": o.get("decision_id"),
            "opportunity_key": o.get("opportunity_key"),
            "as_of": o.get("ts"), "as_of_ms": o.get("ts_ms"),
            "symbol": o.get("symbol"), "direction": o.get("direction"),
            "setup": o.get("setup"), "regime": o.get("regime"),
            "entry_policy_version": o.get("policy_version"),
            "formula_id": FORMULA_ID,
            "p_win_prior": p_pr, "p_win_prior_provenance": "plan.p_win (head heuristiği)",
            "p_gate": p_gate, "p_gate_provenance": p_gate_src,
            "p_win_model": p_md,
            "p_win_model_provenance": ("LearnerV2.predict baseline (champion model YOK → "
                                       "hiyerarşik önsel + legacy harman) + PAPER_BOUNDED etkisi"),
            "avg_win_r": w, "avg_loss_r": ell,
            "cost_r": dec.get("cost_r"), "uncertainty_penalty_r": dec.get("uncertainty_r"),
            "soft_penalty_r": dec.get("soft_penalty_r"),
            "gates_blocked": dec.get("gates_blocked"),
            "production_gross_expectancy_r": _f(o.get("gross_expectancy_r")),
            "production_conservative_net_edge_r": _f(o.get("conservative_net_edge_r")),
            "production_size_multiplier": _f(o.get("size_multiplier")),
            "linked_trade_id": tid,
            "canonical_accepted": bool(o.get("baseline_accepted")),
            "canonical_reject_reasons": o.get("reject_reasons"),
            "canonical_outcome_available": bool(can),
            "evidence_class": POINT_IN_TIME,
        }
        if dec.get("state") != "OK" or w is None or ell is None:
            rows.append({**base, "state": "MISSING_INPUTS",
                         "missing": [k for k, v in (("payoff", w is None or ell is None),
                                                    ("edge_decomposition",
                                                     dec.get("state") != "OK")) if v]})
            continue
        cost_r = dec["cost_r"] or 0.0
        unc = dec["uncertainty_r"] or 0.0
        soft = dec["soft_penalty_r"] or 0.0
        row = {**base, "state": "OK"}
        if p_gate is None:
            row["reproduced_prior_edge"] = None
            row["prior_edge_missing_reason"] = ("p_win_prior kayıtta YOK ve kimlik tersinden "
                                                "de çıkarılamadı")
            row["tradeable_prior"] = None
        else:
            rep = _recompute(p_gate, w, ell, cost_r=cost_r, unc=unc, soft=soft)
            row["reproduced_prior_edge"] = rep
            row["reproduces_production"] = bool(
                base["production_conservative_net_edge_r"] is not None
                and abs(rep["conservative_net_edge_r"]
                        - base["production_conservative_net_edge_r"]) <= 1e-5)
            row["tradeable_prior"] = _tradeable(rep["conservative_net_edge_r"],
                                               dec.get("gates_blocked"))
        if p_md is None:
            row["model_edge"] = None
            row["model_edge_missing_reason"] = "p_win (model) kayıtta YOK"
            row["tradeable_model"] = None
        elif compatibility == INCOMPATIBLE:
            row["model_edge"] = None
            row["model_edge_missing_reason"] = INCOMPATIBLE
            row["tradeable_model"] = None
        else:
            rep = _recompute(p_md, w, ell, cost_r=cost_r, unc=unc, soft=soft)
            row["model_edge"] = rep
            row["model_edge_compatibility"] = compatibility
            row["tradeable_model"] = _tradeable(rep["conservative_net_edge_r"],
                                               dec.get("gates_blocked"))
        rows.append(row)
    return rows


def dual_gate_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Dört eşleşmiş sayaç + eksikler + anlaşmazlık örnekleri."""
    both = [r for r in rows if r.get("tradeable_prior") is not None
            and r.get("tradeable_model") is not None]
    tt = [r for r in both if r["tradeable_prior"] and r["tradeable_model"]]
    tf = [r for r in both if r["tradeable_prior"] and not r["tradeable_model"]]
    ft = [r for r in both if not r["tradeable_prior"] and r["tradeable_model"]]
    ff = [r for r in both if not r["tradeable_prior"] and not r["tradeable_model"]]
    missing_prior = sum(1 for r in rows if r.get("tradeable_prior") is None)
    missing_model = sum(1 for r in rows if r.get("tradeable_model") is None)
    return {
        "schema_version": SCHEMA_VERSION,
        "denominator_all_rows": len(rows),
        "n_both_gates_computable": len(both),
        "counts": {"prior_YES_model_YES": len(tt), "prior_YES_model_NO": len(tf),
                   "prior_NO_model_YES": len(ft), "prior_NO_model_NO": len(ff)},
        "missing": {"tradeable_prior_missing": missing_prior,
                    "tradeable_model_missing": missing_model},
        "disagreement_rate": (round((len(tf) + len(ft)) / len(both), 4) if both else None),
        "disagreement_examples": [
            {"candidate_id": r["candidate_id"], "decision_id": r.get("decision_id"),
             "symbol": r["symbol"], "as_of": r["as_of"],
             "prior_edge": (r.get("reproduced_prior_edge") or {}).get("conservative_net_edge_r"),
             "model_edge": (r.get("model_edge") or {}).get("conservative_net_edge_r"),
             "tradeable_prior": r["tradeable_prior"], "tradeable_model": r["tradeable_model"],
             "canonical_accepted": r["canonical_accepted"],
             "canonical_reject_reasons": r.get("canonical_reject_reasons")}
            for r in (tf + ft)[:20]],
        "economic_gate_only": ("bu sayaçlar YALNIZ ekonomik kapı verdiktidir; kaldıraç kapısı, "
                               "risk kapasitesi ve chief izni AYRI kapılardır ve burada "
                               "değerlendirilmez"),
        "evidence_class": RETROSPECTIVE_RESEARCH,
    }


# --------------------------------------------------------------------- 6) dondurulmuş etiket
def frozen_label(sim: dict[str, Any] | None) -> dict[str, Any]:
    """Hedef-uyumlu etiket: model olasılığının öngördüğü olayla AYNI tanım.

    Kural `learn/labels.py::label_outcome` ile aynıdır (SCRATCH eşiği 0.25R) ve YALNIZ
    kuralla çözülmüş (stop/hedef) simülasyonlara uygulanır. Ufuk sonunda kapanan pozisyon
    **PENDING_CENSORED**tir — kazanç/kayıp sayılmaz.
    """
    if not sim or sim.get("state") not in ("RESOLVED",):
        return {"label": PENDING, "label_rule": LABEL_RULE_ID,
                "reason": (sim or {}).get("state") or "NO_SIM"}
    r = _f(sim.get("net_r"))
    if r is None:
        return {"label": PENDING, "label_rule": LABEL_RULE_ID, "reason": "net_r yok"}
    if abs(r) < SCRATCH_R:
        lab = SCRATCH
    else:
        lab = WIN if r > 0 else LOSS
    return {"label": lab, "label_rule": LABEL_RULE_ID, "net_r": r,
            "evidence_class": RETROSPECTIVE_RESEARCH}


def past_only_base_rate(canonical_closed: list[dict[str, Any]], as_of_ms: float) -> dict[str, Any]:
    """`as_of` ANINDAN ÖNCE kapanmış kanonik işlemlerden temel oran — değerlendirme
    sonuçlarından TÜRETİLMEZ.

    Bu, olasılık skorlarının aşması gereken naif taban çizgisidir.
    """
    prior_rows = [h for h in canonical_closed
                  if (_f(h.get("closed_at_ms")) or math.inf) < as_of_ms]
    labs = []
    for h in prior_rows:
        r = _f(h.get("r_multiple"))
        if r is None:
            continue
        if abs(r) < SCRATCH_R:
            labs.append(0.0)
        else:
            labs.append(1.0 if r > 0 else 0.0)
    if not labs:
        return {"state": "NO_PRIOR_CLOSES", "n": 0, "base_rate": None}
    return {"state": "OK", "n": len(labs), "base_rate": round(sum(labs) / len(labs), 6),
            "rule": LABEL_RULE_ID, "note": "yalnız as_of'tan ÖNCE kapanmış kanonik işlemler"}


def scoring(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """Brier + log loss + eşleşmiş ayrıştırma (AUC yerine basit sıralama istatistiği)."""
    if len(pairs) < 5:
        return {"state": "INSUFFICIENT_SAMPLE", "n": len(pairs)}
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    eps = 1e-12
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y in pairs) / n
    pos = [p for p, y in pairs if y > 0.5]
    neg = [p for p, y in pairs if y <= 0.5]
    auc = None
    if pos and neg:
        wins = sum(1 for a in pos for b in neg if a > b) + \
            0.5 * sum(1 for a in pos for b in neg if a == b)
        auc = round(wins / (len(pos) * len(neg)), 4)
    return {"state": "RUN", "n": n, "n_pos": len(pos), "n_neg": len(neg),
            "brier": round(brier, 6), "log_loss": round(ll, 6),
            "auc": auc, "mean_score": round(sum(p for p, _ in pairs) / n, 6),
            "observed_rate": round(sum(y for _, y in pairs) / n, 6),
            "dispersion_sd": round(
                math.sqrt(sum((p - sum(q for q, _ in pairs) / n) ** 2 for p, _ in pairs) / n), 6)}


__all__ = ["COMPATIBLE", "FORMULA_ID", "INCOMPATIBLE", "LABEL_RULE_ID", "LOSS",
           "MIN_TRADE_MULTIPLIER", "PARTIAL_COMPATIBLE", "PENDING", "RESEARCH_MULTIPLIER",
           "SCHEMA_VERSION", "SCRATCH", "SCRATCH_R", "SOFT_CAP", "UNCERTAINTY_K", "WIN",
           "availability_table", "classify_separation", "decompose_edge", "dual_edge_rows",
           "dual_gate_counts", "frozen_label", "past_only_base_rate", "pipeline_trace",
           "calibration_v2", "probability_contracts", "prior_recovery_check", "recover_prior",
           "scoring", "LABEL_HORIZON_HOURS"]


# --------------------------------------------------- 7) hedef-uyumlu kalibrasyon (v2, düzeltme)
#: Etiketleme için ORTAK gözlem ufku (saat). Kohortun HER üyesi tam olarak bu kadar bar görür.
LABEL_HORIZON_HOURS = 72.0


def calibration_v2(opps: list[dict[str, Any]], sim_rows: list[dict[str, Any]], *,
                   canonical_closed: list[dict[str, Any]]) -> dict[str, Any]:
    """AYNI gözlemler, AYNI etiket tanımı, AYNI ufuk — üç tahmin ediciyle.

    Düzeltme: v1 raporunda kalibrasyon, model olasılığının öngördüğü olayla UYUŞMAYAN bir
    etiket (48 saatlik SABİT ufukta pozitif getiri) üzerinde ölçülmüştü. Burada etiket
    `learn/labels.py` ile aynı kuraldır ve YALNIZ kuralla çözülmüş (stop/hedef) gözlemlere
    uygulanır; ufuk sonunda kapananlar **PENDING_CENSORED**tır ve hesaba GİRMEZ.

    Taban çizgisi (`past_only_base_rate`) değerlendirme sonuçlarından TÜRETİLMEZ: her aday için
    yalnız `as_of`tan ÖNCE kapanmış kanonik işlemlerden hesaplanır.
    """
    by_key = {r.get("opportunity_key"): r for r in sim_rows}
    pairs_prior: list[tuple[float, float]] = []
    pairs_model: list[tuple[float, float]] = []
    pairs_base: list[tuple[float, float]] = []
    labels: dict[str, int] = {}
    n_no_prior = n_no_base = 0
    for o in opps:
        lab = frozen_label(by_key.get(o.get("opportunity_key")))
        labels[lab["label"]] = labels.get(lab["label"], 0) + 1
        if lab["label"] in (PENDING, SCRATCH):
            continue
        y = 1.0 if lab["label"] == WIN else 0.0
        p_md = _f(o.get("p_win_model"))
        p_pr = _f(o.get("p_win_prior"))
        if p_md is not None:
            pairs_model.append((p_md, y))
        if p_pr is not None:
            pairs_prior.append((p_pr, y))
        else:
            n_no_prior += 1
        br = past_only_base_rate(canonical_closed, _f(o.get("ts_ms")) or 0.0)
        if br.get("base_rate") is not None:
            pairs_base.append((float(br["base_rate"]), y))
        else:
            n_no_base += 1
    return {
        "schema_version": SCHEMA_VERSION, "evidence_class": RETROSPECTIVE_RESEARCH,
        "label_rule": LABEL_RULE_ID,
        "label_horizon_hours": LABEL_HORIZON_HOURS,
        "label_distribution": labels,
        "n_labelled": len(pairs_model),
        "censoring_rate": (round(labels.get(PENDING, 0) / max(1, sum(labels.values())), 4)),
        "scores": {
            "p_win_model": scoring(pairs_model),
            "p_win_prior": scoring(pairs_prior),
            "past_only_base_rate": scoring(pairs_base),
        },
        "coverage": {"prior_missing_on_labelled": n_no_prior,
                     "base_rate_missing_on_labelled": n_no_base,
                     "note": ("prior ve model FARKLI alt kümelerde ölçülür; skorlar doğrudan "
                              "karşılaştırılamaz — kesişim ayrıca verilir")},
        "caveats": [
            "Ortalama olasılığın toplam kazanma oranına yakın olması kalibrasyon DEĞİLDİR; "
            "ayrıştırma (AUC/reliability) ayrı bir özelliktir.",
            "Kohort tek bir kısa pencerede ve ağırlıklı LONG; bağımlılık yüksektir.",
            "Sansürlenen gözlemler dışlandığı için kalan küme HIZLI çözülenlere kayar.",
            "Taban çizgisi kanonik işlemlerden gelir; aday evreni FARKLI bir popülasyondur.",
        ],
    }
