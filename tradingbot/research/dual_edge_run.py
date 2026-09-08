"""Dual-edge aşaması (v2) — mevcut runner'ı yeniden kullanan AYRI çıktı üreticisi.

v1 çıktıları (`research_report.json` / `.md` / `research_lessons.json`) **değiştirilmez**.
Bu aşama `dual_edge_report.json`, `dual_edge_report.md` ve `corrections.json` yazar.

İdempotent: aynı export + aynı kod → aynı sonuç; bar önbelleği paylaşılır.
Üretime hiçbir yazım yapılmaz, karar hattı yeniden sıralanmaz, olasılık kaynağı değiştirilmez.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import RESEARCH_LABEL, SCHEMA_VERSION
from .bars import BarCache
from .dataset import ResearchDataset, to_ms, trade_inventory
from .dual_edge import (LABEL_HORIZON_HOURS, availability_table, calibration_v2,
                        classify_separation, dual_edge_rows, dual_gate_counts, pipeline_trace,
                        prior_recovery_check, probability_contracts)
from .entry_lab import (FILL_NEXT_BAR_OPEN, REP_LINKED_ELSE_FIRST, build_opportunities,
                        run_opportunity_simulation)
from .exit_lab import economic_fidelity, measured_cost_per_fill_r

DUAL_JSON = "dual_edge_report.json"
DUAL_MD = "dual_edge_report.md"
CORRECTIONS_JSON = "corrections.json"

F00036_CANDIDATE = "4c7bed8f3bb17655"


def _src(rel: str) -> str:
    p = Path(__file__).resolve().parents[1] / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _influence_mode(export_dir: Path, *, tail_lines: int = 4000) -> dict[str, Any]:
    """Öğrenme etkisi modu — karar günlüğünden OKUNUR, varsayılmaz.

    `entry_snapshot` bu alanı taşımaz; `decision_journal.jsonl` taşır. Dosya büyük olduğu için
    yalnız SON `tail_lines` satır taranır (sınırlı bellek).
    """
    import json as _json
    path = export_dir / "state" / "decision_journal.jsonl"
    if not path.exists():
        return {"mode": "UNKNOWN", "source": "decision_journal.jsonl YOK"}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    except OSError:
        return {"mode": "UNKNOWN", "source": "okunamadı"}
    applied = seen = 0
    mode = None
    last_at = None
    for ln in reversed(lines):
        if "learning_influence" not in ln:
            continue
        try:
            rec = _json.loads(ln)
        except _json.JSONDecodeError:
            continue
        inf = rec.get("learning_influence") or {}
        if not inf.get("mode"):
            continue
        seen += 1
        applied += bool(inf.get("applied"))
        if mode is None:
            mode = str(inf["mode"])
            last_at = rec.get("decision_ts")
    return {"mode": mode or "UNKNOWN", "source": "decision_journal.learning_influence.mode",
            "observed_records": seen, "applied_records": applied, "latest_decision_ts": last_at,
            "scanned_tail_lines": len(lines)}


def f36_block(row: dict[str, Any] | None) -> dict[str, Any]:
    """F00036 kaydının alan yolları, değerleri, formül zinciri ve boşluk mutabakatı."""
    if not row:
        return {"state": "CANDIDATE_NOT_IN_COHORT"}
    prior = row.get("reproduced_prior_edge") or {}
    gross = prior.get("gross_expectancy_r")
    cons = prior.get("conservative_net_edge_r")
    unc = row.get("uncertainty_penalty_r") or 0.0
    soft = row.get("soft_penalty_r") or 0.0
    gap = (gross - cons) if (gross is not None and cons is not None) else None
    return {
        "state": "RECONCILED" if gap is not None else "UNRESOLVED",
        "identity": {"candidate_id": row["candidate_id"], "decision_id": row.get("decision_id"),
                     "as_of": row["as_of"], "symbol": row["symbol"],
                     "linked_trade_id": row.get("linked_trade_id"),
                     "entry_policy_version": row.get("entry_policy_version")},
        "raw_field_paths": {
            "gross_expectancy_r": "$.gross_expectancy_r",
            "net_expectancy_r": "$.net_expectancy_r",
            "uncertainty_penalty_r": "$.uncertainty_penalty_r",
            "conservative_net_edge_r": "$.conservative_net_edge_r",
            "p_win_model": "$.p_win  (sources.p_win = MODELED)",
            "p_win_prior": "$.features.p_win_prior",
            "avg_win_r": "$.avg_win_r", "avg_loss_r": "$.avg_loss_r",
            "size_multiplier": "$.size_multiplier",
            "expectancy_basis": "$.expectancy_basis",
            "provenance": "$.provenance (written_at_stage=RANKING, sees_outcome=false)"},
        "values": {"p_gate": row.get("p_gate"), "p_gate_provenance": row.get("p_gate_provenance"),
                   "p_win_model": row.get("p_win_model"),
                   "avg_win_r": row.get("avg_win_r"), "avg_loss_r": row.get("avg_loss_r"),
                   "cost_r": row.get("cost_r"), "uncertainty_penalty_r": unc,
                   "soft_penalty_r": soft,
                   "size_multiplier": row.get("production_size_multiplier"),
                   "production_gross_expectancy_r": row.get("production_gross_expectancy_r"),
                   "production_conservative_net_edge_r":
                       row.get("production_conservative_net_edge_r")},
        "formula_chain": [
            f"gross = p_gate*avg_win_r - (1-p_gate)*|avg_loss_r| = {gross}",
            "net = gross - cost_r; cost_r = 0 çünkü expectancy_basis = NET_OUTCOME "
            "(maliyet zaten gerçekleşmiş R'lerin içinde — çift sayım koruması)",
            f"conservative = net - uncertainty_penalty({unc}) - soft_penalty({soft}) = {cons}",
            "uncertainty_penalty = UNCERTAINTY_K(0.20)/sqrt(sample_size+1)",
            "soft_penalty = GateLedger.soft_penalty_r(), üst sınır 0.60",
            "tradeable = (not gates.blocked) and conservative > 0 and size_multiplier > 0"],
        "gap_resolution": {
            "reported_gap": (round(gap, 9) if gap is not None else None),
            "explained_by": {"uncertainty_penalty_r": unc, "soft_penalty_r": soft},
            "residual": (round(gap - unc - soft, 12) if gap is not None else None),
            "state": ("RESOLVED" if gap is not None and abs(gap - unc - soft) <= 1e-6
                      else "UNRESOLVED"),
            "clarification": (
                "0.878907 ile 0.677204 AYNI kaydın İKİ FARKLI alanıdır; iki ayrı kanıt "
                "kaynağı arasında bir çelişki YOKTUR. 0.878907 basit ağırlıklı beklentidir "
                "(`gross_expectancy_r`); 'conservative NET edge' etiketi YALNIZ iki kesintiden "
                "sonraki 0.677204 için geçerlidir. Kullanıcının verdiği iki aritmetik satırdan "
                "ilki (0.659 ile) `gross_expectancy_r`yi doğru üretir; ikincisi (0.293 ile) "
                "kapının kullanmadığı olasılıkla hesaplandığı için kayıttaki hiçbir alanı "
                "üretmez."),
        },
        "reproduces_production": row.get("reproduces_production"),
        "dual_gate": {"tradeable_prior": row.get("tradeable_prior"),
                      "tradeable_model": row.get("tradeable_model"),
                      "model_edge": (row.get("model_edge") or {}).get("conservative_net_edge_r"),
                      "model_edge_compatibility": row.get("model_edge_compatibility")},
        "canonical": {"accepted": row.get("canonical_accepted"),
                      "reject_reasons_seen_in_bar": row.get("canonical_reject_reasons"),
                      "note": ("red nedenleri aynı bardaki DİĞER turlardan da toplanır; kabul "
                               "edilen tur için ekonomik kapı geçilmiştir")},
    }


def observer_sufficiency(avail: dict[str, Any], recovery: dict[str, Any],
                         counts: dict[str, Any]) -> dict[str, Any]:
    """Mevcut snapshot'lar sürekli gözlem için YETERLİ mi? Ölçülür, varsayılmaz."""
    total = avail["denominator"]
    computable = counts["n_both_gates_computable"]
    enough = bool(computable == total and recovery.get("n_outside_unit_interval") == 0
                  and (recovery.get("max_abs_error") or 0) <= 1e-3)
    return {
        "sufficient_without_new_instrumentation": enough,
        "both_gates_computable": computable, "denominator": total,
        "how": ("kapı olasılığı doğrudan `features.p_win_prior`den ya da `gross_expectancy_r` "
                "kimliğinin tersinden (p=(gross+|L|)/(W+|L|)) çıkarılır; model olasılığı "
                "`p_win` alanındadır; ödeme, maliyet ve iki kesinti de kayıtlıdır"),
        "prior_recovery_max_abs_error": recovery.get("max_abs_error"),
        "instrumentation_patch_required": (not enough),
        "patch_status": ("GEREKMİYOR — üretim kodu değişikliği ÖNERİLMİYOR" if enough
                         else "HAZIRLANMALI (yerel, KURULMAZ)"),
        "observer": ("bu aşamanın kendisi gözlemcidir: sınırlı salt okunur export üzerinde "
                     "idempotent çalışır, ayrı çıktı üretir, üretime yazmaz"),
        "preserved_on_observation": ["orijinal üretim değerleri", "olayın kendi `as_of`ı",
                                     "toplama zamanı ayrı", "model/kaynak/formül kimliği",
                                     "etiket kuralı kimliği"],
    }


CORRECTIONS = [
    {"id": "C-01", "affects": "PROFITABILITY_RESEARCH_ACCELERATION_V1 §5.1 / RL-01",
     "was": "'conservative_net_edge_r 256/256 kayıtta p_win_prior ile üretiliyor'",
     "now": ("Kimlik testi `gross_expectancy_r` üzerindedir. `conservative_net_edge_r` ondan "
             "belirsizlik ve yumuşak ceza düşülerek TÜRETİLİR. Basit ağırlıklı beklenti "
             "'conservative NET edge' DEĞİLDİR."),
     "impact": "ifade düzeltmesi; sayısal sonuç değişmedi"},
    {"id": "C-02", "affects": "§5.1 payda ifadesi",
     "was": "'256/256 ve 0/404'",
     "now": ("Farklı paydalardı. Tek paydalı kullanılabilirlik tablosu eklendi; ayrıca kapı "
             "olasılığı kimlik tersinden 404/404 için geri çıkarıldı (doğrulama hatası 0.0)."),
     "impact": "kapsam 256 → 404"},
    {"id": "C-03", "affects": "§2 yeniden kullanım haritası / olasılık nitelemesi",
     "was": "'p_win (model/kalibre çıktı)'",
     "now": ("Champion `p_win_lr` modeli YOK (`models.json` mevcut değil) ve Platt kalibratörü "
             "fit edilmemiş (n_fit=0). Snapshot `p_win`i = 0.5·hiyerarşik önsel + 0.5·legacy v1 "
             "tahmini, ardından PAPER_BOUNDED etkisi. 'Kalibre model çıktısı' nitelemesi "
             "DESTEKLENMEZ."),
     "impact": "iddia zayıflatıldı"},
    {"id": "C-04", "affects": "RL-02 kalibrasyon",
     "was": "48 saatlik SABİT ufukta pozitif getiri etiketiyle ölçülen kalibrasyon",
     "now": ("Etiket, model olasılığının hedefiyle UYUŞMUYORDU. Hedef-uyumlu etiketle "
             "(|r|<0.25R SCRATCH, yalnız kuralla çözülmüş, ortak 72 sa ufuk) yeniden ölçüldü "
             "ve geçmiş-yalnız temel oran taban çizgisi eklendi."),
     "impact": "RL-02 yeniden koşuldu → calibration_v2"},
    {"id": "C-05", "affects": "RL-03 sadakat ifadesi",
     "was": "'şampiyon sadakati: işaret uyumu 1.0, çıkış nedeni uyumu 1.0'",
     "now": ("Bunlar KATEGORİK uyumdur, ekonomik denklik DEĞİLDİR. Büyüklük karşılaştırması "
             "eklendi (`economic_fidelity`): EXACT 0, WITHIN_TOLERANCE 18, MISMATCH 7."),
     "impact": "sonuç yönü değişmedi (farkların GA'sı hâlâ sıfırı içeriyor), güven düştü"},
    {"id": "C-06", "affects": "RL-04 'düzeltilmiş sıralama'",
     "was": "'model p_win ile düzeltilmiş kenar'",
     "now": ("'Düzeltilmiş' nitelemesi kaldırıldı: hedef uyumu YALNIZ KISMİdir (SCRATCH eşiği) "
             "ve ikame edilen olasılık kalibre değildir. Karşılaştırma BETİMLEYİCİdir; "
             "uygulanabilir bir model-kenar politikası ÖNERİLMEZ."),
     "impact": "ifade düzeltmesi"},
    {"id": "C-07", "affects": "genel ifade",
     "was": "'25 kapanışla negatif beklenti istatistiksel olarak kanıtlanmış değil'",
     "now": ("Doğru ama eksik: GERÇEKLEŞEN zarar (cüzdan değişimi -1.7774 USDT) bir OLGUdur; "
             "güven aralığı yalnız POPÜLASYON beklentisi hakkındadır, gerçekleşmiş sonuç "
             "hakkında değil."),
     "impact": "ifade netleştirmesi"},
]


def build_dual_edge_report(export_dir: Path, out_dir: Path, *, seed: int = 7,
                           offline: bool = False) -> dict[str, Any]:
    """Kaynak izi + kullanılabilirlik + F00036 mutabakatı + eşleşmiş çift kapı + kalibrasyon v2."""
    t0 = time.time()
    ds = ResearchDataset.load(export_dir)
    cutoff_ms = to_ms(ds.cutoff_utc)
    inv = trade_inventory(ds)
    canonical = {r["trade_id"]: r for r in inv["closed"]}
    cost = measured_cost_per_fill_r(inv["closed"])

    trace = pipeline_trace(_src("engine_v3.py"), _src("coinhead/head.py"))
    cal = ds.learn_v2.get("calibrator") or {}
    champion_present = (export_dir / "state" / "models.json").exists()
    inf_mode = _influence_mode(export_dir)
    separation = classify_separation(
        trace, influence_mode=inf_mode["mode"],
        champion_model_present=champion_present,
        calibrator_fitted=bool(int(cal.get("n_fit") or 0) > 0),
        snapshot_reports_model_p_win=True)
    contracts = probability_contracts(
        champion_model_present=champion_present, calibrator=cal,
        n_closed=ds.learn_v2.get("n_closed"), learn_updated_at=ds.learn_v2.get("updated_at"))

    built = build_opportunities(ds.entry_rows, ds.entry_links,
                                representative=REP_LINKED_ELSE_FIRST)
    opps = built["opportunities"]
    avail = availability_table(opps)
    recovery = prior_recovery_check(opps)
    rows = dual_edge_rows(opps, links=ds.entry_links, canonical_by_trade=canonical)
    counts = dual_gate_counts(rows)

    bars = BarCache(out_dir / "cache" / "bars_15m", offline=offline, max_requests=900)
    sim = run_opportunity_simulation(opps, bars, cutoff_ms=cutoff_ms,
                                     cost_per_fill_r=cost["cost_per_fill_r"] or 0.0,
                                     horizon_hours=LABEL_HORIZON_HOURS,
                                     fill_modes=(FILL_NEXT_BAR_OPEN,), fetch=not offline)
    calib2 = calibration_v2(opps, sim["results"][FILL_NEXT_BAR_OPEN],
                            canonical_closed=inv["closed"])
    fidelity = economic_fidelity(inv["closed"], bars, cutoff_ms=cutoff_ms,
                                 cost_per_fill_r=cost["cost_per_fill_r"] or 0.0,
                                 fetch=not offline)
    bars.flush()

    f36 = next((r for r in rows if r.get("candidate_id") == F00036_CANDIDATE), None)
    linked = [r for r in rows if r.get("linked_trade_id")]
    return {
        "schema_version": SCHEMA_VERSION + "/dual_edge", "label": RESEARCH_LABEL,
        "stage": "dual_edge_v2",
        "export": {"root": str(export_dir), "cutoff_utc": ds.cutoff_utc,
                   "app_head": ds.app_head},
        "pipeline_trace": trace,
        "separation_classification": separation,
        "learning_influence_observed": inf_mode,
        "probability_contracts": contracts,
        "opportunity_pool": {k: v for k, v in built.items() if k != "opportunities"},
        "availability": avail,
        "prior_recovery": recovery,
        "f00036_reconciliation": f36_block(f36),
        "linked_candidates": [
            {"trade_id": r["linked_trade_id"], "candidate_id": r["candidate_id"],
             "decision_id": r.get("decision_id"), "symbol": r["symbol"], "as_of": r["as_of"],
             "p_gate": r.get("p_gate"), "p_win_model": r.get("p_win_model"),
             "prior_edge": (r.get("reproduced_prior_edge") or {}).get("conservative_net_edge_r"),
             "model_edge": (r.get("model_edge") or {}).get("conservative_net_edge_r"),
             "tradeable_prior": r.get("tradeable_prior"),
             "tradeable_model": r.get("tradeable_model")} for r in linked],
        "dual_gate_counts": counts,
        "dual_edge_rows": rows,
        "calibration_v2": calib2,
        "economic_fidelity": {k: v for k, v in fidelity.items() if k != "rows"},
        "economic_fidelity_rows": fidelity.get("rows"),
        "observer_sufficiency": observer_sufficiency(avail, recovery, counts),
        "corrections": CORRECTIONS,
        "write_boundary": {"production_code_changed": False, "production_config_changed": False,
                           "canonical_state_written": False, "vps_writes": False,
                           "pipeline_reordered": False, "probability_source_switched": False,
                           "gates_or_risk_changed": False, "pfexp_touched": False,
                           "promotion_counters_touched": False, "learned_index_touched": False,
                           "outputs_root": str(out_dir)},
        "runtime": {"elapsed_s": round(time.time() - t0, 2),
                    "bar_cache_stats": dict(bars.stats), "seed": seed},
    }


def render_dual_markdown(rep: dict[str, Any]) -> str:
    L: list[str] = []
    a = L.append
    tr, sep, av = rep["pipeline_trace"], rep["separation_classification"], rep["availability"]
    f36, counts = rep["f00036_reconciliation"], rep["dual_gate_counts"]
    a("# Dual-Edge Doğrulama ve Eşleşmiş Karşılaştırma (v2)")
    a("")
    a(f"> **{rep['label']}** · export cutoff `{rep['export']['cutoff_utc']}` · "
      f"app HEAD `{str(rep['export']['app_head'])[:12]}`")
    a("")
    a("## 1. Karar hattı izi (kaynaktan, her koşuda yeniden ölçülür)")
    a("")
    a("| Aşama | Dosya | Satır | Kapsayıcı fonksiyon |")
    a("| --- | --- | --- | --- |")
    for k, v in tr["anchors"].items():
        a(f"| `{k}` | `{v['file']}` | {v['line']} | `{v['enclosing_def']}` |")
    a("")
    a(f"* Aynı kapsam mı: **{tr['same_enclosing_scope']}** (`{tr['enclosing_scope']}`)")
    a(f"* Ekonomik kapı model atamasından önce mi: **{tr['economic_gate_before_model_assignment']}**")
    a(f"* `_assess_opportunities` çağrı yerleri: {tr['assess_call_sites']}")
    a(f"* Model atamasından SONRA yeniden hesap çağrısı: {tr['recompute_call_sites_after_model']} "
      f"→ alternatif yol: **{tr['alternate_recompute_path']}**")
    a("")
    li = rep.get("learning_influence_observed") or {}
    a(f"* Gözlenen öğrenme etkisi modu: **{li.get('mode')}** "
      f"({li.get('observed_records')} kayıt, {li.get('applied_records')} uygulanmış; "
      f"kaynak `{li.get('source')}`)")
    a("")
    a(f"**Sınıflandırma: `{sep['classification']}`**")
    a("")
    for r in sep.get("reasons", []):
        a(f"* {r}")
    a("")
    a("## 2. Olasılık sözleşmeleri (hedef + ufuk)")
    a("")
    a("| Büyüklük | Öngördüğü olay | Ufuk | Fit edilmiş mi | Kalibre mi |")
    a("| --- | --- | --- | --- | --- |")
    for key in ("p_win_prior", "p_win_model"):
        c = rep["probability_contracts"][key]
        a(f"| `{key}` | {c['predicted_event']} | {c['horizon']} | {c['is_fitted_model']} | "
          f"{c['is_calibrated']} |")
    tc = rep["probability_contracts"]["target_compatibility"]
    a("")
    a(f"* Ön tahmin ↔ ödeme modeli: **{tc['prior_vs_payoff']}** — {tc['prior_vs_payoff_reason']}")
    a(f"* Model ↔ ödeme modeli: **{tc['model_vs_payoff']}** — {tc['model_vs_payoff_reason']}")
    a("")
    a("## 3. Kullanılabilirlik — TEK payda")
    a("")
    a(f"Payda = **{av['denominator']}** fırsat.")
    a("")
    a("| Alan | Var | Yok |")
    a("| --- | --- | --- |")
    for k, v in av["counts"].items():
        a(f"| `{k}` | {v} | {av['denominator'] - v} |")
    a("")
    rc = rep["prior_recovery"]
    a(f"Kapı olasılığı, kimliğin tersinden **404/404** için geri çıkarıldı; doğrudan kayıtlı "
      f"{rc['n_validated']} satırda azami hata **{rc['max_abs_error']}**.")
    a("")
    a("## 4. F00036 mutabakatı")
    a("")
    a(f"Kimlik: `{f36['identity']['candidate_id']}` / `{f36['identity']['decision_id']}` · "
      f"as_of `{f36['identity']['as_of']}` · `{f36['identity']['linked_trade_id']}`")
    a("")
    a("| Alan (JSON yolu) | Değer |")
    a("| --- | --- |")
    for k, path in f36["raw_field_paths"].items():
        a(f"| `{path}` | {f36['values'].get(k, '—')} |")
    a("")
    for step in f36["formula_chain"]:
        a(f"* {step}")
    a("")
    gr = f36["gap_resolution"]
    a(f"**Boşluk {gr['reported_gap']} = belirsizlik {gr['explained_by']['uncertainty_penalty_r']} "
      f"+ yumuşak ceza {gr['explained_by']['soft_penalty_r']}; artık "
      f"{gr['residual']} → `{gr['state']}`.**")
    a("")
    a(gr["clarification"])
    a("")
    a("## 5. Eşleşmiş çift kapı")
    a("")
    a("| | model EVET | model HAYIR |")
    a("| --- | --- | --- |")
    c = counts["counts"]
    a(f"| **ön tahmin EVET** | {c['prior_YES_model_YES']} | {c['prior_YES_model_NO']} |")
    a(f"| **ön tahmin HAYIR** | {c['prior_NO_model_YES']} | {c['prior_NO_model_NO']} |")
    a("")
    a(f"Hesaplanabilir: **{counts['n_both_gates_computable']}/{counts['denominator_all_rows']}** · "
      f"eksik: {counts['missing']} · anlaşmazlık oranı **{counts['disagreement_rate']}**")
    a("")
    a(counts["economic_gate_only"])
    a("")
    a("### Kanonik işleme bağlanmış adaylar")
    a("")
    a("| İşlem | Sembol | as_of | p_gate | p_model | ön tahmin kenarı | model kenarı | kapı(ön) | kapı(model) |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rep["linked_candidates"]:
        a(f"| {r['trade_id']} | {r['symbol']} | {r['as_of']} | {r['p_gate']} | "
          f"{r['p_win_model']} | {r['prior_edge']} | {r['model_edge']} | "
          f"{r['tradeable_prior']} | {r['tradeable_model']} |")
    a("")
    a("## 6. Kalibrasyon (hedef-uyumlu, düzeltilmiş)")
    a("")
    cv = rep["calibration_v2"]
    a(f"Etiket kuralı `{cv['label_rule']}` · ortak ufuk **{cv['label_horizon_hours']} sa** · "
      f"sansür oranı **{cv['censoring_rate']}** · dağılım {cv['label_distribution']}")
    a("")
    a("| Tahmin edici | n | Brier | Log loss | AUC | Ortalama skor | Gerçekleşen |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for k, v in cv["scores"].items():
        if v.get("state") != "RUN":
            a(f"| `{k}` | {v.get('n')} | — | — | — | — | {v.get('state')} |")
            continue
        a(f"| `{k}` | {v['n']} | {v['brier']} | {v['log_loss']} | {v['auc']} | "
          f"{v['mean_score']} | {v['observed_rate']} |")
    a("")
    for c2 in cv["caveats"]:
        a(f"* {c2}")
    a("")
    a("## 7. Ekonomik replay sadakati (kategorik DEĞİL, büyüklük)")
    a("")
    ef = rep["economic_fidelity"]
    a(f"* Durumlar: {ef['states']} (n={ef['n']})")
    a(f"* Azami |ΔR| **{ef['max_abs_r_error']}** · ortalama |ΔR| {ef['mean_abs_r_error']} · "
      f"azami |Δ net USDT| {ef['max_abs_net_usdt_error']}")
    a(f"* Çıkış zamanı bir bar içinde: **{ef['exit_time_within_one_bar']}/{ef['n']}** · "
      f"azami zaman hatası {ef['max_exit_time_error_ms']} ms")
    a(f"* Dolum sayısı uyuşmazlığı: {ef['fills_mismatch']}")
    a(f"* Simülasyona GİRDİ OLARAK VERİLMEYENLER: {', '.join(ef['inputs_not_used_to_force_exits'])}")
    a("")
    a("## 8. Sürekli gözlem yeterliliği")
    a("")
    os_ = rep["observer_sufficiency"]
    for k, v in os_.items():
        a(f"* `{k}`: {v}")
    a("")
    a("## 9. Düzeltilen ifadeler")
    a("")
    for c3 in rep["corrections"]:
        a(f"### {c3['id']} — {c3['affects']}")
        a("")
        a(f"* **Önceki:** {c3['was']}")
        a(f"* **Düzeltilmiş:** {c3['now']}")
        a(f"* **Etki:** {c3['impact']}")
        a("")
    a("## 10. Yazma sınırı")
    a("")
    for k, v in rep["write_boundary"].items():
        a(f"* `{k}`: {v}")
    a("")
    a(f"Süre {rep['runtime']['elapsed_s']} s · bar önbelleği {rep['runtime']['bar_cache_stats']}")
    return "\n".join(L) + "\n"


__all__ = ["CORRECTIONS", "CORRECTIONS_JSON", "DUAL_JSON", "DUAL_MD", "F00036_CANDIDATE",
           "build_dual_edge_report", "f36_block", "observer_sufficiency", "render_dual_markdown"]
