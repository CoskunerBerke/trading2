"""Onarım hazırlığı + hedefe hizalı ödeme karşılaştırması aşaması (v4) — AYRI çıktı.

v1/v2/v3 çıktıları DEĞİŞTİRİLMEZ. Bu aşama `readiness_report.json` / `.md` yazar.

İçerik: üretim revizyonu ile yerel düzeltmenin AYRIMI, A/B durum kararı kanıtı, dönüştürücü
kuru çalıştırması ve bağımsız doğrulaması, provenans değişikliği ve ödeme modeli
karşılaştırması. Üretime hiçbir yazım yapılmaz.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import RESEARCH_LABEL, SCHEMA_VERSION
from .dataset import ResearchDataset, trade_inventory
from .entry_lab import REP_LINKED_ELSE_FIRST, build_opportunities
from .learn_state_repair import (analyse, convert, diff_summary, option_comparison,
                                 verify_conversion)
from .payoff_model import (PANEL_BLEND, PANEL_PRIOR, accounting_reconciliation,
                           minus_one_approximation_check, payoff_comparison, r_definition,
                           trace_avg_win_r)

READINESS_JSON = "readiness_report.json"
READINESS_MD = "readiness_report.md"

#: Onarımın hedeflediği üretim revizyonu (salt okunur doğrulandı).
PRODUCTION_HEAD = "12db804c7301dbebe337813e20e44c9e967d260b"

#: Sürüm adayı ağacındaki DEĞİŞEN üretim dosyaları (araştırma modülleri HARİÇ).
RELEASE_FILES = [
    "tradingbot/learn/model.py",
    "tradingbot/learn/learner_v2.py",
    "tradingbot/learn/reconcile.py",
    "tradingbot/dashboard/views.py",
    "tradingbot/dashboard/app.py",
    "tradingbot/dashboard/state.py",
    "tests/test_learning_count_integrity.py",
    "tests/test_learning_provenance.py",
]


def deployment_status(vps_head: str, *, count_fix_present: bool,
                      presentation_fix_present: bool) -> dict[str, Any]:
    """Yerel düzeltme ile ÇALIŞAN üretimi AÇIKÇA ayırır."""
    return {
        "vps_head": vps_head,
        "expected_production_head": PRODUCTION_HEAD,
        "head_matches": vps_head == PRODUCTION_HEAD,
        "count_fix_deployed": count_fix_present,
        "presentation_fix_deployed": presentation_fix_present,
        "statement": ("Sayaç ve sunum düzeltmeleri ÜRETİMDE DEĞİL. Yerel testlerin yeşil olması "
                      "çalışan botun öğrenmesinin onarıldığı ANLAMINA GELMEZ."),
        "release_branch": "release/learn-count-integrity",
        "release_files": RELEASE_FILES,
        "research_modules_excluded": True,
    }


def build_readiness_report(export_dir: Path, out_dir: Path, *,
                           learn_state_path: Path | None = None,
                           vps_head: str = PRODUCTION_HEAD,
                           count_fix_deployed: bool = False,
                           presentation_fix_deployed: bool = False) -> dict[str, Any]:
    t0 = time.time()
    ds = ResearchDataset.load(export_dir)
    inv = trade_inventory(ds)
    closes = inv["closed"]
    n_closes = len(closes)
    n_wins = sum(1 for c in closes if (c.get("r_multiple") or 0) > 0)

    snap_path = learn_state_path or (export_dir / "immutable" / "learn_v2.snapshot.json")
    doc = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {}

    an = analyse(doc, canonical_closes=n_closes, canonical_wins=n_wins) if doc else {
        "state": "NO_STATE_FILE"}
    conv = convert(doc, canonical_closes=n_closes, canonical_wins=n_wins) if doc else {}
    ver = verify_conversion(doc, conv) if doc else {}
    opts = option_comparison(doc) if doc else {}

    opps = build_opportunities(ds.entry_rows, ds.entry_links,
                               representative=REP_LINKED_ELSE_FIRST)["opportunities"]
    payoff = {p: payoff_comparison(opps, closes, panel=p)
              for p in (PANEL_PRIOR, PANEL_BLEND)}

    return {
        "schema_version": SCHEMA_VERSION + "/readiness", "label": RESEARCH_LABEL,
        "stage": "readiness_v4",
        "export": {"root": str(export_dir), "cutoff_utc": ds.cutoff_utc,
                   "app_head": ds.app_head},
        "deployment_status": deployment_status(vps_head,
                                               count_fix_present=count_fix_deployed,
                                               presentation_fix_present=presentation_fix_deployed),
        "state_snapshot": {"path": str(snap_path), "sha256": an.get("input_sha256"),
                           "canonical_closes": n_closes, "canonical_wins": n_wins},
        "repair_analysis": an,
        "repair_diff": conv.get("diff"),
        "repair_diff_text": diff_summary(conv) if conv else "",
        "repair_verification": ver,
        "option_comparison": opts,
        "payoff": {
            "r_definition": r_definition(),
            "avg_win_r_trace": trace_avg_win_r(),
            "minus_one_check": minus_one_approximation_check(closes),
            "accounting": accounting_reconciliation(closes),
            "panels": {p: {k: v for k, v in payoff[p].items() if k != "rows"}
                       for p in payoff},
            "rows_prior": payoff[PANEL_PRIOR]["rows"],
        },
        "write_boundary": {"production_state_written": False, "vps_writes": False,
                           "deployed": False, "model_fitted": False,
                           "canonical_history_rewritten": False, "pfexp_touched": False,
                           "outputs_root": str(out_dir)},
        "runtime": {"elapsed_s": round(time.time() - t0, 2)},
    }


def render_readiness_markdown(rep: dict[str, Any]) -> str:
    L: list[str] = []
    a = L.append
    ds_ = rep["deployment_status"]
    an, ver, opt = rep["repair_analysis"], rep["repair_verification"], rep["option_comparison"]
    a("# Onarım Hazırlığı ve Hedefe Hizalı Ödeme Karşılaştırması (v4)")
    a("")
    a(f"> **{rep['label']}** · export cutoff `{rep['export']['cutoff_utc']}`")
    a("")
    a("## 1. Yerel düzeltme ≠ çalışan üretim")
    a("")
    a(f"* VPS HEAD `{ds_['vps_head']}` · beklenen `{ds_['expected_production_head']}` · "
      f"eşleşiyor: **{ds_['head_matches']}**")
    a(f"* Sayaç düzeltmesi ÜRETİMDE: **{ds_['count_fix_deployed']}**")
    a(f"* Sunum düzeltmesi ÜRETİMDE: **{ds_['presentation_fix_deployed']}**")
    a("")
    a(f"**{ds_['statement']}**")
    a("")
    a(f"Sürüm dalı `{ds_['release_branch']}` · araştırma modülleri HARİÇ "
      f"({ds_['research_modules_excluded']}).")
    a("")
    a("## 2. Durum onarımı — kanıt")
    a("")
    a(f"Anlık görüntü `{rep['state_snapshot']['path']}` · sha256 "
      f"`{str(rep['state_snapshot']['sha256'])[:16]}` · kanonik kapanış "
      f"{rep['state_snapshot']['canonical_closes']} / kazanç {rep['state_snapshot']['canonical_wins']}")
    a("")
    a(f"**Durum: `{an.get('state')}`** ({an.get('n_ancestor_nodes')} ata, "
      f"{an.get('n_leaf_nodes')} yaprak)")
    a("")
    a("| Kontrol | Sonuç | Ayrıntı |")
    a("| --- | --- | --- |")
    for c in an.get("checks", []):
        a(f"| `{c['check']}` | {'GEÇTİ' if c['passed'] else 'DÜŞTÜ'} | {c['detail']} |")
    a("")
    a("### Kuru çalıştırma farkı")
    a("")
    a("```")
    a(rep.get("repair_diff_text", ""))
    a("```")
    a("")
    a(f"**Bağımsız doğrulama: `{ver.get('state')}`** — "
      f"saklanan durum ↔ derslerden ESKİ replay: "
      f"{(ver.get('stored_matches_old_replay') or {}).get('identical')}; "
      f"dönüştürülmüş ↔ derslerden YENİ replay: "
      f"{(ver.get('converted_matches_new_replay') or {}).get('identical')}; "
      f"`exp_r` dokunulmadı: {(ver.get('exp_r_untouched') or {}).get('identical')}")
    a("")
    a("## 3. A / B seçenek karşılaştırması")
    a("")
    a(f"*{opt.get('label')}* · çürüme var mı: **{opt.get('decay_present')}**")
    a("")
    a(opt.get("permanence", ""))
    a("")
    a("| gelecek kapanış | A n | A posterior | A ceza | B n | B posterior | B ceza | Δposterior | fazla kütle |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in opt.get("rows", []):
        a(f"| {r['future_closes']} | {r['A_global_n']} | {r['A_posterior']} | "
          f"{r['A_uncertainty_penalty_r']} | {r['B_global_n']} | {r['B_posterior']} | "
          f"{r['B_uncertainty_penalty_r']} | {r['delta_posterior_B_minus_A']} | "
          f"{r['excess_mass_in_A']} |")
    a("")
    a("## 4. Ödeme modeli — hedefe hizalı koşullu ortalamalar")
    a("")
    pf = rep["payoff"]
    rd = pf["r_definition"]
    a("**R tanımı:**")
    for k in ("initial_risk_denominator", "partial_exit_aggregation", "costs", "exit_policy",
              "target_threshold", "scratch"):
        a(f"* `{k}`: {rd[k]}")
    a("")
    tr = pf["avg_win_r_trace"]
    a(f"**Mevcut `avg_win_r` sınıfı: `{tr['verdict']}`** — {tr['formula']}")
    a("")
    a(tr["why_not_an_empirical_conditional_mean"])
    a("")
    mo = pf["minus_one_check"]
    a(f"**−1R yaklaşımı:** E[R | kazanç değil] = **{mo['mean_R_given_not_win']}**, "
      f"SCRATCH {mo['n_scratch']} → `{mo['direction']}`")
    a("")
    ac = pf["accounting"]
    a(f"**Muhasebe:** net {ac['sum_net_pnl']} = brüt {ac['sum_gross_pnl']} − ücret "
      f"{ac['sum_fees']} + funding {ac['sum_funding']}; artık {ac['residual']} "
      f"(mutabık {ac['reconciled']}) → maliyet TEKRAR düşülmez.")
    a("")
    a("| Panel (olasılık SABİT) | kapsam | ort. Δgross | azami \\|Δ\\| | eski EVET→yeni HAYIR | eski HAYIR→yeni EVET |")
    a("| --- | --- | --- | --- | --- | --- |")
    for p, v in pf["panels"].items():
        gd = v["gate_disagreement"]
        a(f"| `{p}` | {v['coverage_fraction']} | {v['mean_delta_gross']} | "
          f"{v['max_abs_delta_gross']} | {gd['old_YES_new_NO']} | {gd['old_NO_new_YES']} |")
    a("")
    for c in (list(pf["panels"].values())[0].get("caveats") or []):
        a(f"* {c}")
    a("")
    a("## 5. Yazma sınırı")
    a("")
    for k, v in rep["write_boundary"].items():
        a(f"* `{k}`: {v}")
    a("")
    a(f"Süre {rep['runtime']['elapsed_s']} s")
    return "\n".join(L) + "\n"


__all__ = ["PRODUCTION_HEAD", "READINESS_JSON", "READINESS_MD", "RELEASE_FILES",
           "build_readiness_report", "deployment_status", "render_readiness_markdown"]
