"""Öğrenme bütünlüğü + düzeltilmiş olasılık kıyaslaması aşaması (v3) — AYRI çıktı.

v1 (`research_report.*`) ve v2 (`dual_edge_report.*`) çıktıları **değiştirilmez**.
Bu aşama `integrity_report.json` / `integrity_report.md` yazar.

Üretim `learn_v2.json`'ına, kanonik deftere, pfexp dosyalarına ve öğrenilmiş indekse
DOKUNULMAZ; hiçbir model fit edilmez, hiçbir state göç ettirilmez.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import RESEARCH_LABEL, SCHEMA_VERSION
from .bars import BarCache
from .dataset import ResearchDataset, to_ms, trade_inventory
from .dual_edge import dual_edge_rows, past_only_base_rate
from .entry_lab import REP_LINKED_ELSE_FIRST, build_opportunities
from .exit_lab import measured_cost_per_fill_r
from .learning_replay import (NEW, OLD, count_integrity, edge_sensitivity,
                              point_in_time_components)
from .probability_benchmark import (TARGET_A, TARGET_B, build_targets, label_target_contract,
                                    run_benchmark)

INTEGRITY_JSON = "integrity_report.json"
INTEGRITY_MD = "integrity_report.md"


def _closes_with_features(ds: ResearchDataset, inv: dict[str, Any]) -> list[dict[str, Any]]:
    """Envanter satırlarına defterdeki `features` ve `setup_type` alanlarını geri ekler."""
    by_id = {str(h.get("id")): h for h in ds.closed_trades}
    out = []
    for r in inv["closed"]:
        h = by_id.get(str(r["trade_id"]), {})
        out.append(dict(r, features=(h.get("features") or {}),
                        setup_type=h.get("setup_type")))
    return out


def state_repair_plan(ci: dict[str, Any], pit: dict[str, Any]) -> dict[str, Any]:
    """Üretimdeki öğrenilmiş durum için ONARIM PLANI — HAZIRLANIR, UYGULANMAZ.

    Plan yalnız `state/learn_v2.json` içindeki `win` düğüm sayaçlarını kapsar; kanonik defter,
    hafıza (`trade_memory.jsonl`), pfexp dosyaları ve öğrenilmiş indeks KAPSAM DIŞIdır.
    """
    dup = ci.get("duplicated_nodes") or []
    return {
        "status": "PREPARED_NOT_EXECUTED",
        "scope": "yalnız state/learn_v2.json -> win.stats (ata düğümler)",
        "out_of_scope": ["futures_ledger.json", "trade_memory.jsonl",
                         "profitability_experiment*", "learned index", "modeller"],
        "contaminated_nodes": dup, "n_contaminated": len(dup),
        "options": [
            {"id": "R1", "name": "DOKUNMA (varsayılan öneri)",
             "what": ("kod düzeltildi; bundan SONRAKİ kapanışlar tek kez sayılır. Mevcut "
                      "sayaçlar olduğu gibi kalır ve zamanla seyrelir."),
             "risk": "düşük", "reversible": True,
             "effect": ("geçmiş 25 kapanışın ata düğümlerdeki ağırlığı iki kat kalır; "
                        "belirsizlik cezası bir süre daha olduğundan KÜÇÜK olur")},
            {"id": "R2", "name": "ATA DÜĞÜMLERİ YENİDEN KUR",
             "what": ("kanonik kapanışlardan `win` hiyerarşisini düzeltilmiş semantikle "
                      "yeniden kur; yaprak düğümler zaten doğru olduğu için değişmez"),
             "risk": "orta",
             "blocker": ("üretimde düğüm anahtarı KAPANIŞ ANI rejimiyle kurulur ve bu değer "
                         "kalıcı saklanmaz → rejim düğümleri birebir yeniden kurulamaz "
                         "(bkz. reconstruction_check.limitation)"),
             "precondition": ["yedek + sha256", "worker durdurulmuş", "operatör onayı",
                              "yeniden kurulan değerlerin eski değerlerle yan yana raporu"],
             "reversible": True, "executed": False},
            {"id": "R3", "name": "KAPANIŞ ANI REJİMİNİ KALICI YAP",
             "what": ("`on_trade_closed` çağrısındaki `decision_snapshot.regime` kapanışla "
                      "birlikte saklansın → hiyerarşi kanonik kayıtlardan yeniden üretilebilir"),
             "risk": "düşük ama DAVRANIŞ/ŞEMA değişikliği",
             "scope_note": "bu görevin kapsamı DIŞINDA — yalnız kaydedildi", "executed": False},
        ],
        "recommendation": ("R1 — sayaç kusuru kodda düzeltildi; mevcut durumu yeniden yazmak "
                           "R2'nin blocker'ı yüzünden birebir doğrulanamaz ve kazancı küçüktür "
                           f"(ortalama belirsizlik cezası farkı "
                           f"{(pit.get('effect') or {}).get('mean_delta_uncertainty_penalty_r')} R)"),
        "execution_authorized": False,
    }


def build_integrity_report(export_dir: Path, out_dir: Path, *, seed: int = 7,
                           offline: bool = False) -> dict[str, Any]:
    t0 = time.time()
    ds = ResearchDataset.load(export_dir)
    cutoff_ms = to_ms(ds.cutoff_utc)
    inv = trade_inventory(ds)
    closed = _closes_with_features(ds, inv)
    cost = measured_cost_per_fill_r(inv["closed"])
    built = build_opportunities(ds.entry_rows, ds.entry_links,
                                representative=REP_LINKED_ELSE_FIRST)
    opps = built["opportunities"]

    ci = count_integrity(closed)
    pit = point_in_time_components(closed, opps)
    dual = dual_edge_rows(opps, links=ds.entry_links,
                          canonical_by_trade={r["trade_id"]: r for r in inv["closed"]})
    sens = edge_sensitivity(dual, pit)

    bars = BarCache(out_dir / "cache" / "bars_15m", offline=offline, max_requests=900)
    targets = build_targets(opps, bars, cutoff_ms=cutoff_ms,
                            cost_per_fill_r=cost["cost_per_fill_r"] or 0.0,
                            fetch=not offline)

    def _base(ts_ms: float) -> float | None:
        return past_only_base_rate(inv["closed"], ts_ms).get("base_rate")

    bench = {t: run_benchmark(opps, targets, base_rate_fn=_base, target=t, seed=seed)
             for t in (TARGET_A, TARGET_B)}
    bars.flush()

    return {
        "schema_version": SCHEMA_VERSION + "/integrity", "label": RESEARCH_LABEL,
        "stage": "integrity_v3",
        "export": {"root": str(export_dir), "cutoff_utc": ds.cutoff_utc,
                   "app_head": ds.app_head},
        "cohort": {
            "raw_snapshot_rows": built["n_raw_rows"],
            "opportunity_groups": built["n_unique"],
            "representative_policy": built["representative_policy"],
            "canonical_links": len(ds.entry_links),
            "canonical_closes": inv["summary"]["n_closed"],
            "note": ("örtüşen fırsatlar BAĞIMSIZ işlem DEĞİLDİR; ham satır sayısı örneklem "
                     "büyüklüğü olarak kullanılamaz"),
        },
        "count_integrity": {k: v for k, v in ci.items() if k != "nodes"},
        "count_integrity_nodes": ci["nodes"],
        "point_in_time_components": {k: v for k, v in pit.items() if k != "rows"},
        "point_in_time_rows": pit["rows"],
        "edge_sensitivity": sens,
        "label_target_contract": label_target_contract(),
        "targets": {t: {k: v for k, v in targets[t].items() if k != "rows"}
                    for t in (TARGET_A, TARGET_B)},
        "benchmark": bench,
        "state_repair_plan": state_repair_plan(ci, pit),
        "write_boundary": {"production_learned_state_written": False,
                           "canonical_history_rewritten": False, "model_fitted": False,
                           "state_migrated": False, "vps_writes": False,
                           "pfexp_touched": False, "experiment_created": False,
                           "gates_or_thresholds_changed": False,
                           "outputs_root": str(out_dir)},
        "code_changes": {
            "learn/model.py": "HierarchicalRate.add(leaves=...) + _keys_multi (ata tek sayım)",
            "learn/learner_v2.py": "on_trade_closed tek çağrı, iki yaprak",
            "dashboard/views.py": "kolon adları + _cell_pct_signal + column_notes",
            "dashboard/app.py": "alan anlamları legend'i + kartlarda aynı hassasiyet",
        },
        "runtime": {"elapsed_s": round(time.time() - t0, 2),
                    "bar_cache_stats": dict(bars.stats), "seed": seed},
    }


def render_integrity_markdown(rep: dict[str, Any]) -> str:
    L: list[str] = []
    a = L.append
    ci, pit = rep["count_integrity"], rep["point_in_time_components"]
    a("# Öğrenme Bütünlüğü ve Düzeltilmiş Olasılık Kıyaslaması (v3)")
    a("")
    a(f"> **{rep['label']}** · export cutoff `{rep['export']['cutoff_utc']}` · "
      f"app HEAD `{str(rep['export']['app_head'])[:12]}`")
    a("")
    a("## 1. Kohort (ayrı ayrı)")
    a("")
    for k, v in rep["cohort"].items():
        a(f"* `{k}`: {v}")
    a("")
    a("## 2. Sayaç bütünlüğü")
    a("")
    a(f"*{ci['label']}*")
    a("")
    a("| Ölçüm | Değer |")
    a("| --- | --- |")
    for k in ("n_final_closes", "unique_observations", "update_calls_win_old",
              "update_calls_win_new", "weighted_mass_global_old", "weighted_mass_global_new",
              "exp_r_global_n", "n_duplicated_nodes", "n_unchanged_nodes", "verdict"):
        a(f"| `{k}` | {ci.get(k)} |")
    a("")
    a(f"Yinelenen düğümler: {ci.get('duplicated_nodes')}")
    a("")
    a(ci.get("note", ""))
    a("")
    a("## 3. Point-in-time bileşen etkisi")
    a("")
    rc = pit["reconstruction_check"]
    a(f"* Doğrulama: eski semantik, kayıttaki `sample_size`i "
      f"**{rc['old_semantics_reproduces_recorded_sample_size']}/"
      f"{rc['n_with_recorded_sample_size']}** satırda birebir üretiyor.")
    a(f"* Sınır: {rc['limitation']}")
    a(f"* Sağlam ölçüm: {rc['robust_measure']}")
    a("")
    ef = pit["effect"]
    a("| Etki | Değer |")
    a("| --- | --- |")
    for k, v in ef.items():
        a(f"| `{k}` | {v} |")
    a("")
    s = rep["edge_sensitivity"]
    a(f"Kenar duyarlılığı: n={s['n']}, ortalama Δ={s['mean_delta_edge_r']} R, "
      f"azami |Δ|={s['max_abs_delta_edge_r']} R, **kapı verdikti değişen satır: "
      f"{s['n_gate_verdict_flips']}**.")
    a("")
    a("## 4. Öğrenme hedefinin gerçek sözleşmesi")
    a("")
    lc = rep["label_target_contract"]
    for k in ("class_construction", "win_flag", "hierarchy_update", "classifier_target",
              "scratch_treatment", "conditionality", "horizon", "payoff_mismatch"):
        a(f"* **{k}**: {lc[k]}")
    a("")
    a("## 5. Eşleşmiş olasılık kıyaslaması")
    a("")
    for t, b in rep["benchmark"].items():
        a(f"### {t}")
        a("")
        if b.get("state") != "RUN":
            a(f"`{b.get('state')}` — {b.get('reason')}")
            a("")
            continue
        a(f"* Kural: {b['target_rule']}")
        a(f"* Etiket: `{b['label_rule']}`")
        a(f"* Kohort **{b['n_cohort']}**, sansürlü **{b['n_censored']}**, "
          f"eksik tahmin edici {b['n_missing_predictor']}")
        a(f"* Kimlik kümesi sha256 `{b['id_set_sha256']}` — üç tahmin edici için AYNI "
          f"({b['n_per_predictor']})")
        a(f"* Ön tahmin kaynağı: {b['prior_provenance']}")
        a(f"* Kohortta SCRATCH sonuç: {b['n_scratch_outcomes_in_cohort']} (paydada NOT_WIN)")
        a("")
        a("| Tahmin edici | n | Brier ↓ | Log loss ↓ | AUC | skor sd | gerçekleşen |")
        a("| --- | --- | --- | --- | --- | --- | --- |")
        for k, v in b["scores"].items():
            if v.get("state") != "RUN":
                a(f"| `{k}` | {v.get('n')} | — | — | — | — | {v.get('state')} |")
                continue
            a(f"| `{k}` | {v['n']} | {v['brier']} | {v['log_loss']} | "
              f"{v['auc']} ({v['auc_state']}) | {v['score_sd']} | {v['observed_rate']} |")
        a("")
        a(f"Brier sıralaması: {b['brier_ranking']}")
        a("")
        a("| Eşleşmiş Brier farkı | ortalama | GA95 (sembol kümesi) | sıfırı dışlıyor |")
        a("| --- | --- | --- | --- |")
        for k, v in b["paired_brier_delta"].items():
            if v.get("state") != "RUN":
                a(f"| {k} | — | — | {v.get('state')} |")
                continue
            a(f"| {k} | {v['mean_delta']} | {v['ci95']} | "
              f"{'EVET' if v['excludes_zero'] else 'HAYIR'} |")
        a("")
    a("## 6. Üretim durumu onarım planı")
    a("")
    rp = rep["state_repair_plan"]
    a(f"**{rp['status']}** · kapsam: {rp['scope']} · kirlenen düğüm: {rp['n_contaminated']}")
    a("")
    for o in rp["options"]:
        a(f"* **{o['id']} — {o['name']}**: {o['what']} (risk {o['risk']}"
          + (f"; blocker: {o['blocker']}" if o.get("blocker") else "") + ")")
    a("")
    a(f"Öneri: {rp['recommendation']}")
    a("")
    a("## 7. Değişen kod")
    a("")
    for k, v in rep["code_changes"].items():
        a(f"* `{k}` — {v}")
    a("")
    a("## 8. Yazma sınırı")
    a("")
    for k, v in rep["write_boundary"].items():
        a(f"* `{k}`: {v}")
    a("")
    a(f"Süre {rep['runtime']['elapsed_s']} s · bar önbelleği {rep['runtime']['bar_cache_stats']}")
    return "\n".join(L) + "\n"


__all__ = ["INTEGRITY_JSON", "INTEGRITY_MD", "NEW", "OLD", "build_integrity_report",
           "render_integrity_markdown", "state_repair_plan"]
