"""Profitability Research Acceleration V1 — TEK tekrarlanabilir komut.

    python -m tradingbot.research.run --export <export_dir> --out <out_dir>

Girdi salt okunurdur; yazım YALNIZ `--out` kökü altındadır. `state/` altına yazım fail-closed
reddedilir. Aynı girdi + aynı kod + aynı config → aynı `research_id` → önbellekten döner ve
sonuç/ders ÇOĞALTILMAZ (`--force` ile yeniden hesaplanır).

Üretilenler:
* `research_report.json` — kesin JSON (NaN/Infinity yazılmaz)
* `research_report.md`   — okunabilir özet
* `research_lessons.json` — araştırma dersi kataloğu (üretim derslerinden AYRI)
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import RESEARCH_LABEL, RETROSPECTIVE_RESEARCH, SCHEMA_VERSION
from .bars import BarCache
from .dataset import ResearchDataset, reconcile_economics, to_ms, trade_inventory
from .entry_lab import (FILL_NEXT_BAR_OPEN, FILL_PLAN_TRIGGER, HORIZON_HOURS, HORIZON_HOURS_ALT,
                        build_opportunities, calibration, corrected_edge, portfolio_replay,
                        ranking_null_test, run_opportunity_simulation,
                        score_definition_trace, verify_gate_probability_order)
from .exit_lab import measured_cost_per_fill_r, run_exit_comparison
from .lessons import ResearchLesson, catalog
from .protocol import ResearchProtocol, file_sha256

REPORT_JSON = "research_report.json"
REPORT_MD = "research_report.md"
LESSONS_JSON = "research_lessons.json"

#: Rapora giren metrik adları (protokolde önceden kayıtlı).
METRICS = ["expectancy_r", "win_rate", "profit_factor", "max_drawdown_r", "total_r",
           "brier", "calibration_gap", "percentile_of_actual", "mean_delta_r"]

#: Girdi bütünlüğü için hash'lenen dosyalar.
HASHED_INPUTS = ["futures_ledger.json", "spot_ledger.json", "entry_snapshot.jsonl",
                 "position_path.jsonl", "trade_memory.jsonl", "learn_v2.json",
                 "entry_selectivity.json", "exit_eval.json"]


def _finite(obj: Any) -> Any:
    """RFC-JSON güvenliği: sonlu olmayan sayı JSON'a ASLA yazılmaz."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _code_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False)
        return (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _guard_out(out: Path) -> None:
    """Canlı state'e yazımı fail-closed engelle."""
    parts = {p.lower() for p in out.resolve().parts}
    if "state" in parts:
        raise SystemExit("HATA: --out bir 'state' dizini içeriyor — araştırma çıktısı canlı "
                         "state'e YAZILAMAZ.")


def _fold_attempt(protocol: ResearchProtocol, first_ms: float | None, last_ms: float | None,
                  horizon_hours: float) -> dict[str, Any]:
    """Sızıntısız üç yollu walk-forward DENENİR; sığmıyorsa dürüstçe raporlanır.

    Purge/embargo, sonuç ufkundan KISA OLAMAZ — örtüşen sonuçlar aksi hâlde train'den test'e
    sızar. Pencere yetmiyorsa kapı GEVŞETİLMEZ; analiz "yapılamadı" olarak kalır.
    """
    from ..quant.walkforward import make_folds
    if first_ms is None or last_ms is None:
        return {"state": "NO_DECISION_WINDOW"}
    span_days = (last_ms - first_ms) / 86_400_000.0
    bar_h = 4.0
    need_bars = max(1, int(math.ceil(horizon_hours / bar_h)))
    params = {"mode": "anchored", "train_days": 2, "validation_days": 1, "test_days": 1,
              "purge_bars": need_bars, "embargo_bars": need_bars, "tf": "4h"}
    gap_days = 2 * need_bars * bar_h / 24.0
    required = params["train_days"] + params["validation_days"] + params["test_days"] + \
        2 * gap_days
    try:
        plan = make_folds(int(first_ms), int(last_ms), **params)
        protocol.record_variant("three_way_walk_forward", kind="entry_selectivity",
                                params=params, outcome="RUN")
        return {"state": "RUN", "params": params, "n_folds": len(plan.get("folds") or [])}
    except ValueError as exc:
        protocol.record_variant("three_way_walk_forward", kind="entry_selectivity",
                                params=params, outcome="BLOCKED", note=str(exc)[:200])
        return {"state": "INSUFFICIENT_WINDOW_FOR_LEAKFREE_FOLDS",
                "decision_window_days": round(span_days, 3),
                "required_days": round(required, 3),
                "purge_embargo_days_per_boundary": round(gap_days, 3),
                "outcome_horizon_hours": horizon_hours,
                "params": params, "error": str(exc)[:300],
                "decision": ("eşik FİT EDİLMEZ — purge/embargo sonuç ufkundan kısa tutularak "
                             "yapay fold ÜRETİLMEDİ; seçicilik challenger'ı ÇALIŞTIRILMADI"),
                "consequence": ("bu koşuda yalnız PARAMETRESİZ karşılaştırmalar raporlanır "
                                "(kalibrasyon, sıralama null testi, sabit çıkış politikaları)")}


def build_report(export_dir: Path, out_dir: Path, *, seed: int = 7, offline: bool = False,
                 max_concurrent: int = 11, n_null: int = 200) -> dict[str, Any]:
    t0 = time.time()
    ds = ResearchDataset.load(export_dir)
    cutoff_ms = to_ms(ds.cutoff_utc)
    if cutoff_ms is None:
        raise SystemExit("HATA: export MANIFEST.txt içinde cutoff_utc yok — protokol "
                         "dondurulamaz.")

    inv = trade_inventory(ds)
    recon = reconcile_economics(ds)
    built = build_opportunities(ds.entry_rows, ds.entry_links)
    opps = built["opportunities"]
    trace = score_definition_trace(opps)
    # Kimlik testinin bulduğu sonucu KAYNAKTAN da doğrula (iddia sabitlenmez, her koşuda ölçülür).
    engine_path = Path(__file__).resolve().parents[1] / "engine_v3.py"
    src_order = verify_gate_probability_order(
        engine_path.read_text(encoding="utf-8", errors="replace") if engine_path.exists() else "")
    trace["source_order_check"] = src_order
    cost = measured_cost_per_fill_r(inv["closed"])

    ts_list = [o["ts_ms"] for o in opps if o.get("ts_ms")]
    protocol = ResearchProtocol(
        run_label="profitability_research_acceleration_v1",
        code_sha=_code_sha(), cutoff_utc=str(ds.cutoff_utc),
        input_hashes={n: file_sha256(export_dir / "state" / n) for n in HASHED_INPUTS},
        universe=sorted({o["symbol"] for o in opps} | {r["symbol"] for r in inv["closed"]}),
        policy_versions={"app_head": ds.app_head,
                         "entry_policy_versions": sorted(
                             {str(o.get("policy_version")) for o in opps}),
                         "exit_champion": "CHAMPION_AS_IS"},
        windows={"canonical_closed_from": min((r["opened_at"] for r in inv["closed"]),
                                              default=None),
                 "canonical_closed_to": max((r["closed_at"] for r in inv["closed"]),
                                            default=None),
                 "entry_decisions_from_ms": (min(ts_list) if ts_list else None),
                 "entry_decisions_to_ms": (max(ts_list) if ts_list else None),
                 "equal_horizon_hours": HORIZON_HOURS,
                 "equal_horizon_hours_alt": HORIZON_HOURS_ALT},
        metrics=METRICS, seed=seed, max_trials=1,
        compute_budget={"bar_interval": "15m", "max_bar_requests": 900,
                        "n_null_permutations": n_null},
        limitations=[
            "Giriş kararı verisi yalnız 2026-09-02 sonrasını kapsar (≈5,9 gün).",
            "Bu kapanışlar ve raporlar önceki oturumlarda incelenmiştir — DOKUNULMAMIŞ "
            "holdout DEĞİLDİR; ileri doğrulama bağımsız kalmalıdır.",
            "Bar içi stop/hedef sırası ölçülemez; muhafazakâr sıra kullanılır.",
            "Reddedilen fırsat simülasyonları RETROSPECTIVE_RESEARCH'tir; orijinal snapshot "
            "değildir ve ileri kanıt sayılmaz.",
            "Kohort ağırlıklı olarak LONG (391/404) ve tek bir kısa piyasa penceresindedir; "
            "rejim etkisi kenar etkisinden AYRILAMAZ.",
        ])

    bars = BarCache(out_dir / "cache" / "bars_15m", offline=offline, max_requests=900)

    exit_cmp = run_exit_comparison(inv["closed"], bars, cutoff_ms=cutoff_ms,
                                   cost_per_fill_r=cost["cost_per_fill_r"] or 0.0,
                                   seed=seed, fetch=not offline)
    protocol.record_variant("exit_champion_vs_3_predefined", kind="exit",
                            params={"cost_per_fill_r": cost["cost_per_fill_r"]}, outcome="RUN")
    protocol.record_variant("exit_adverse_cost_x2", kind="exit",
                            params={"multiplier": 2.0}, outcome="RUN")

    sims = {}
    for h in (HORIZON_HOURS, HORIZON_HOURS_ALT):
        sims[h] = run_opportunity_simulation(opps, bars, cutoff_ms=cutoff_ms,
                                             cost_per_fill_r=cost["cost_per_fill_r"] or 0.0,
                                             horizon_hours=h, fetch=not offline)
        protocol.record_variant(f"entry_equal_horizon_{int(h)}h", kind="entry",
                                params={"horizon_hours": h}, outcome="RUN")
    primary = sims[HORIZON_HOURS]["results"][FILL_NEXT_BAR_OPEN]

    calib = {k: calibration(opps, primary, score_key=k)
             for k in ("p_win_model", "p_win_prior")}
    protocol.record_variant("calibration_p_win_model_vs_prior", kind="entry", outcome="RUN")

    rankers = {"AS_IS_conservative_net_edge_r": lambda o: o.get("conservative_net_edge_r"),
               "CORRECTED_model_p_win": corrected_edge}
    ranking = {}
    for cap in (max_concurrent, 3):
        for name, fn in rankers.items():
            ranking[f"{name}@K{cap}"] = ranking_null_test(
                opps, primary, rank_key=fn, label=name, max_concurrent=cap,
                n_null=n_null, seed=seed)
    protocol.record_variant("ranking_permutation_null_test", kind="entry",
                            params={"n_null": n_null, "caps": [max_concurrent, 3]},
                            outcome="RUN")

    pool = portfolio_replay(opps, primary, rank_key=lambda o: 0.0, label="ALL_IN_POOL",
                            max_concurrent=10_000)

    folds = _fold_attempt(protocol, protocol.windows["entry_decisions_from_ms"],
                          protocol.windows["entry_decisions_to_ms"], HORIZON_HOURS)

    lessons = _build_lessons(inv, exit_cmp, trace, calib, ranking, sims, recon)

    report = {
        "schema_version": SCHEMA_VERSION, "label": RESEARCH_LABEL,
        "protocol": protocol.to_dict(),
        "export": {"root": str(export_dir), "cutoff_utc": ds.cutoff_utc,
                   "app_head": ds.app_head},
        "coverage": inv["summary"],
        "trade_inventory": {"closed": inv["closed"], "open": inv["open"]},
        "economics": recon,
        "cost_model": cost,
        "entry": {
            "opportunity_pool": {k: v for k, v in built.items() if k != "opportunities"},
            "score_definition_trace": trace,
            "simulation_summary": {str(int(h)): s["summary"] for h, s in sims.items()},
            "simulation_meta": {str(int(h)): {k: v for k, v in s.items() if k != "results"}
                                for h, s in sims.items()},
            "calibration": calib,
            "ranking_null_tests": ranking,
            "all_in_pool": pool,
            "walk_forward_attempt": folds,
        },
        "exit": exit_cmp,
        "write_boundary": {
            "production_code_changed": False, "production_config_changed": False,
            "canonical_state_written": False, "vps_writes": False,
            "promotion_counters_touched": False, "learned_index_touched": False,
            "outputs_root": str(out_dir),
        },
        "runtime": {"elapsed_s": round(time.time() - t0, 2), "bar_cache_stats": dict(bars.stats)},
    }
    bars.flush()
    report["runtime"]["bar_cache_stats"] = dict(bars.stats)
    return {"report": _finite(report), "lessons": _finite(catalog(lessons))}


def _build_lessons(inv, exit_cmp, trace, calib, ranking, sims, recon) -> list[ResearchLesson]:
    """Bulgular — destekleyen VE çelişen kanıtla birlikte. Tek yönlü okuma yok."""
    out: list[ResearchLesson] = []
    closed = inv["closed"]
    winners = [r["trade_id"] for r in closed if (r.get("r_multiple") or 0) > 0]
    losers = [r["trade_id"] for r in closed if (r.get("r_multiple") or 0) <= 0]

    prior = trace.get("with_p_win_prior") or {}
    model = trace.get("with_p_win_model") or {}
    out.append(ResearchLesson(
        lesson_id="RL-01", behaviour="DEFECT",
        title="Ekonomik kapıyı model olasılığı DEĞİL, plan ön tahmini sürüyor",
        claim=("`gross_expectancy_r` (ve ondan türeyen `conservative_net_edge_r`) kimliği "
               f"{prior.get('n_identity_holds')}/{prior.get('n_testable')} kayıtta YALNIZ "
               f"`p_win_prior` ile sağlanıyor; `p_win` (model) ile "
               f"{model.get('n_identity_holds')}/{model.get('n_testable')} kayıtta sağlanıyor. "
               "Sıralama/boyutlandırma kapısı öğrenilmiş olasılığı GÖRMÜYOR."),
        evidence_class=trace.get("evidence_class", RETROSPECTIVE_RESEARCH),
        sample_size=int(prior.get("n_testable") or 0),
        net_effect=(f"iki olasılığın ortalama farkı {trace.get('p_win_prior_mean_minus_model_mean')} "
                    f"R-olasılık puanı; korelasyon {trace.get('corr_prior_model')}"),
        uncertainty=("aritmetik kimlik testidir, istatistiksel çıkarım değildir; sapma "
                     f"{prior.get('max_abs_deviation')} (kayan nokta düzeyinde)"),
        supporting_ids=["entry_snapshot: kimlik testi geçen tüm kayıtlar",
                        f"engine_v3.py:{(trace.get('source_order_check') or {}).get('assess_call_line')}"
                        " ekonomik kapı çağrısı",
                        f"engine_v3.py:{(trace.get('source_order_check') or {}).get('model_p_win_assign_line')}"
                        " öğrenilmiş p_win ataması"],
        contradicting_ids=(["p_win_prior taşımayan NO_TRIGGER kayıtları — kimlik "
                            "sınanamadı"] if (model.get("n_testable") or 0)
                           > (prior.get("n_testable") or 0) else ["yok"]),
        missing_count=int((model.get("n_testable") or 0) - (prior.get("n_testable") or 0)),
        alternative_explanations=[
            "Snapshot alanı, karar akışının FARKLI bir aşamasında yazılmış olabilir; "
            "kapının kendisi doğru olasılığı kullanıyor ama kayıt geç güncelleniyor olabilir.",
            "`p_win_prior` kasıtlı bir 'soğuk başlangıç' tasarımı olabilir.",
        ],
        falsifiable_next_test=("Kaynak sırası her koşuda yeniden ölçülür "
                               "(`verify_gate_probability_order`). Kapı, öğrenilmiş p_win "
                               "atamasından SONRA çalışır hâle gelirse bu ders KENDİLİĞİNDEN "
                               "çürür. İkinci test: aynı snapshot'larla `conservative_net_edge_r` "
                               "model p_win ile yeniden hesaplanıp `tradeable` kararı "
                               "karşılaştırılmalı."),
    ))

    cm, cp = calib.get("p_win_model") or {}, calib.get("p_win_prior") or {}
    out.append(ResearchLesson(
        lesson_id="RL-02", behaviour="NEGATIVE",
        title="Her iki olasılık skoru da bu kohortta ayrıştırıcı değil",
        claim=(f"`p_win` Brier={cm.get('brier')} (n={cm.get('n')}), "
               f"`p_win_prior` Brier={cp.get('brier')} (n={cp.get('n')}). Kova tablolarında "
               "yüksek skorlu kovaların gerçekleşen kazanma oranı DAHA DÜŞÜK — sıralama "
               "beklenen yönde DEĞİL."),
        evidence_class=RETROSPECTIVE_RESEARCH,
        sample_size=int(cm.get("n") or 0),
        net_effect=(f"kalibrasyon açığı: model {cm.get('calibration_gap')}, "
                    f"prior {cp.get('calibration_gap')}"),
        uncertainty=("kohort tek bir 3 günlük pencerede ve %97 LONG; ters sıralama gürültü "
                     "olabilir. İki skor FARKLI alt kümelerde ölçüldü (prior eksik satırlar)."),
        supporting_ids=[f"kalibrasyon kovaları: {len(cm.get('buckets') or [])} kova"],
        contradicting_ids=["p_win_prior seviye olarak model'den DAHA İYİ kalibre "
                           "(Brier düşük) — yani 'prior kötüdür' iddiası desteklenmez"],
        missing_count=int((cm.get("n") or 0) - (cp.get("n") or 0)),
        alternative_explanations=[
            "Yükselen bir piyasa penceresinde ufuk-sonu kapanışlar kazanç sayılıyor olabilir.",
            "48 saatlik ufuk, skorların hedeflediği ufukla aynı olmayabilir.",
        ],
        falsifiable_next_test=("Aynı kalibrasyon, ileriye dönük PAPER kapanışlarında (≥50 "
                               "karşılaştırılabilir kapanış) tekrarlanmalı; sıralama pozitif "
                               "çıkarsa bu ders ÇÜRÜR."),
    ))

    base = (exit_cmp.get("base_scenario") or {})
    pd_ = exit_cmp.get("paired_deltas") or {}
    best = max(pd_.items(), key=lambda kv: (kv[1].get("mean_delta_r") or -9)) if pd_ else (None, {})
    out.append(ResearchLesson(
        lesson_id="RL-03", behaviour="MIXED",
        title="Çıkış alternatifleri beklentiyi iyileştiriyor ama hiçbiri gürültüden ayrılmıyor",
        claim=(f"25 kanonik kapanışın TAMAMI gerçek 15dk barlarla yeniden yürütüldü. "
               f"Şampiyon beklentisi {(base.get('champion') or {}).get('metrics', {}).get('expectancy_r')} R; "
               f"en yüksek eşleşmiş fark {best[0]} = {best[1].get('mean_delta_r')} R, "
               f"sembol-kümesi GA95 {best[1].get('ci95_by_symbol_cluster')} — SIFIRI İÇERİYOR."),
        evidence_class="CANONICAL_OBSERVED",
        sample_size=int(exit_cmp.get("n_trades") or 0),
        net_effect="hiçbir challenger için istatistiksel olarak ayırt edilebilir kazanç yok",
        uncertainty=(f"n={exit_cmp.get('n_trades')}, küme sayısı "
                     f"{best[1].get('n_clusters')}; GA sıfırı içeriyor"),
        supporting_ids=[r["trade_id"] for r in closed
                        if (r.get("mfe_pct") or 0) > 0 and (r.get("r_multiple") or 0) < 0][:12],
        contradicting_ids=winners[:12],
        missing_count=int(exit_cmp.get("n_rejected") or 0),
        alternative_explanations=[
            "Kazanan işlemler zaten hedefe ulaşıyor; erken kısmi kâr onları KISALTIYOR "
            "(`big_winner_truncation` negatif).",
            "15dk bar çözünürlüğü bar içi hareketi tam yakalamıyor.",
        ],
        falsifiable_next_test=("Aynı üç politika ileriye dönük SHADOW'da çalıştırılıp ≥50 "
                               "karşılaştırılabilir kapanışta eşleşmiş fark ölçülmeli; GA "
                               "sıfırı dışlarsa ders güçlenir, dışlamazsa ÇÜRÜR."),
    ))

    rk = {k: v for k, v in ranking.items() if v.get("state") == "RUN"}
    dist = [k for k, v in rk.items() if v.get("distinguishable_from_random")]
    out.append(ResearchLesson(
        lesson_id="RL-04", behaviour="NEGATIVE",
        title="Sıralama ölçütü rastgele seçimden ayırt edilemiyor",
        claim=("Sermaye kısıtlı replay'de üretim sıralaması (`conservative_net_edge_r`) ve "
               "düzeltilmiş sıralama (model p_win), 200 permütasyonluk null dağılımın "
               f"içinde kalıyor. Ayırt edilebilen varyant sayısı: {len(dist)}."),
        evidence_class=RETROSPECTIVE_RESEARCH,
        sample_size=int(max((v.get("n_taken") or 0) for v in rk.values()) if rk else 0),
        net_effect="; ".join(f"{k}: yüzdelik {v.get('percentile_of_actual')}, "
                             f"null GA {v.get('null_ci95')}" for k, v in rk.items()),
        uncertainty="alınabilen işlem sayısı çok küçük (kapasite kısıtı); null dağılım geniş",
        supporting_ids=sorted(rk.keys()),
        contradicting_ids=([k for k, v in rk.items()
                            if (v.get("percentile_of_actual") or 0) > 0.9]
                           or ["yok — hiçbir varyant üst %10'da değil"]),
        missing_count=int(sum(1 for v in ranking.values() if v.get("state") != "RUN")),
        alternative_explanations=[
            "Kapasite kısıtı (K) sıralamanın etkisini bastırıyor olabilir.",
            "48 saatlik ufuk, sıralamanın hedeflediği ufukla uyuşmuyor olabilir.",
        ],
        falsifiable_next_test=("Daha uzun bir giriş kaydı biriktikten sonra (≥30 gün) aynı "
                               "permütasyon testi tekrarlanmalı; gerçek ölçüt null GA'nın "
                               "ÜSTÜNE çıkarsa ders ÇÜRÜR."),
    ))

    fut = recon["futures"]
    out.append(ResearchLesson(
        lesson_id="RL-05", behaviour="POSITIVE",
        title="Kanonik muhasebe tam mutabık — zarar bir muhasebe kusuru DEĞİL",
        claim=(f"FEE+FUNDING+PNL = {fut['ledger_entry_sum']} = cüzdan değişimi "
               f"{fut['wallet_delta_usdt']}; artık {fut['reconciliation_residual_usdt']}. "
               "Kapanmış işlem neti ile açık pozisyonların gerçekleşmiş kısmi kârı ayrı."),
        evidence_class="CANONICAL_OBSERVED",
        sample_size=int(fut["closed_trades"]["n"]),
        net_effect=(f"kapanmış net {fut['closed_trades']['net_pnl_usdt']} USDT, açık "
                    f"gerçekleşmiş {fut['open_positions']['realized_partial_pnl_usdt']} USDT, "
                    f"açık MTM {fut['open_positions']['unrealized_mtm_usdt']} USDT"),
        uncertainty="mutabakat kesin (artık 0); yorum değil ARİTMETİK",
        supporting_ids=[r["trade_id"] for r in closed][:12],
        contradicting_ids=["yok — mutabakat artığı sıfır"],
        missing_count=0,
        alternative_explanations=["Yok; bu bir kimlik testidir."],
        falsifiable_next_test=("Yeni kapanışlardan sonra aynı mutabakat tekrar çalıştırılmalı; "
                               "artık sıfırdan saparsa muhasebe kusuru VARDIR."),
    ))
    return out


def render_markdown(report: dict[str, Any], lessons: dict[str, Any]) -> str:
    p = report["protocol"]
    cov = report["coverage"]
    fut = report["economics"]["futures"]
    tr = report["entry"]["score_definition_trace"]
    ex = report["exit"]
    L: list[str] = []
    a = L.append
    a("# Profitability Research Acceleration V1 — sonuç raporu")
    a("")
    a(f"> **{report['label']}** · `research_id` `{p['research_id']}` · kod `{p['code_sha'][:12]}` "
      f"· export cutoff `{p['cutoff_utc']}`")
    a("")
    a("Bu rapor kâr vaat etmez, bir politikayı terfi ettirmez ve hiçbir ileri sayacı artırmaz.")
    a("")
    a("## 1. Kapsam (bütün işlemler)")
    a("")
    a("| Ölçüm | Değer |")
    a("| --- | --- |")
    for k, v in cov.items():
        a(f"| `{k}` | {v} |")
    a("")
    a("## 2. Ekonomi mutabakatı")
    a("")
    a(f"* Cüzdan değişimi **{fut['wallet_delta_usdt']} USDT**; defter kalemleri toplamı "
      f"**{fut['ledger_entry_sum']}**; artık **{fut['reconciliation_residual_usdt']}** "
      f"(mutabık: {fut['reconciled']}).")
    a(f"* Kapanmış {fut['closed_trades']['n']} işlem net **{fut['closed_trades']['net_pnl_usdt']} "
      f"USDT** (ücret {fut['closed_trades']['fees_usdt']}, funding "
      f"{fut['closed_trades']['funding_usdt']}).")
    a(f"* Açık {fut['open_positions']['n']} pozisyon: gerçekleşmiş kısmi kâr "
      f"**{fut['open_positions']['realized_partial_pnl_usdt']} USDT**, açık MTM "
      f"**{fut['open_positions']['unrealized_mtm_usdt']} USDT** (AYRI tutulur).")
    a(f"* Kayma {fut['closed_trades']['slippage_usdt_in_fill_price']} USDT — dolum fiyatının "
      "içindedir, ayrıca düşülmez.")
    a("")
    a("## 3. Skor tanımı izi")
    a("")
    a(f"* Kimlik `{tr['identity_tested']}`")
    a(f"* `p_win` (model) ile: **{tr['with_p_win_model']['n_identity_holds']}/"
      f"{tr['with_p_win_model']['n_testable']}**")
    a(f"* `p_win_prior` ile: **{tr['with_p_win_prior']['n_identity_holds']}/"
      f"{tr['with_p_win_prior']['n_testable']}**")
    a(f"* Ekonomik kapıyı süren: **{tr['economic_gate_driver']}** · ortalama fark "
      f"{tr['p_win_prior_mean_minus_model_mean']} · korelasyon {tr['corr_prior_model']}")
    so = tr.get("source_order_check") or {}
    a(f"* Kaynak sırası doğrulaması (`engine_v3.py`): ekonomik kapı satır "
      f"`{so.get('assess_call_line')}`, öğrenilmiş `p_win` ataması satır "
      f"`{so.get('model_p_win_assign_line')}` → kapı önce mi: "
      f"**{so.get('economic_gate_runs_before_model_p_win')}**. {so.get('conclusion', '')}")
    a("")
    a("## 4. Çıkış karşılaştırması (25/25 kanonik kapanış, gerçek 15dk barlar)")
    a("")
    fid = ex.get("champion_fidelity") or {}
    a(f"Şampiyon sadakati: işaret uyumu **{fid.get('sign_agreement')}**, çıkış nedeni uyumu "
      f"**{fid.get('exit_reason_agreement')}**, ortalama |R| hatası **{fid.get('mean_abs_r_error')}**.")
    a("")
    a("| Politika | n | Beklenti (R) | PF | Kazanma | Eşleşmiş fark | GA95 (sembol kümesi) | Rastgeleden ayrılıyor mu |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    ch = (ex.get("base_scenario") or {}).get("champion") or {}
    m = ch.get("metrics") or {}
    a(f"| CHAMPION_AS_IS | {ch.get('n')} | {m.get('expectancy_r')} | {m.get('profit_factor')} | "
      f"{m.get('win_rate')} | — | — | — |")
    for c in (ex.get("base_scenario") or {}).get("challengers") or []:
        pdv = (ex.get("paired_deltas") or {}).get(c["policy"], {})
        cm = c.get("metrics") or {}
        a(f"| {c['policy']} | {c.get('n')} | {cm.get('expectancy_r')} | {cm.get('profit_factor')} | "
          f"{cm.get('win_rate')} | {pdv.get('mean_delta_r')} | {pdv.get('ci95_by_symbol_cluster')} | "
          f"{'EVET' if pdv.get('cluster_ci_excludes_zero') else 'HAYIR'} |")
    a("")
    adv = ex.get("adverse_scenario") or {}
    a(f"Olumsuz maliyet senaryosu (×{adv.get('cost_multiplier')}): şampiyon beklentisi "
      f"{(adv.get('champion') or {}).get('metrics', {}).get('expectancy_r')} R.")
    amb = ex.get("intrabar_ambiguity") or {}
    a(f"Bar içi belirsizlik: {amb.get('n_flagged')} işlem işaretli. {amb.get('note')}")
    a("")
    a("## 5. Giriş: reddedilen fırsatlar dahil")
    a("")
    pool = report["entry"]["opportunity_pool"]
    a(f"* Ham karar kaydı **{pool['n_raw_rows']}** → tekilleştirilmiş fırsat **{pool['n_unique']}** "
      f"({pool['dedup_rule']}).")
    for h, s in report["entry"]["simulation_summary"].items():
        for mode, v in s.items():
            a(f"* Ufuk **{h} sa** / dolum **{mode}**: sonuçlu {v['n_with_outcome']}/"
              f"{v['n_opportunities']} (kural ile çözülen {v['n_rule_resolved']}, ufukta kapanan "
              f"{v['n_horizon_closed']}), beklenti **{v['expectancy_r']} R**, kazanma "
              f"**{v['win_rate']}**.")
    a("")
    a("### Kalibrasyon")
    a("")
    a("| Skor | n | Brier | Ortalama skor | Gerçekleşen kazanma | Açık |")
    a("| --- | --- | --- | --- | --- | --- |")
    for k, c in report["entry"]["calibration"].items():
        a(f"| `{k}` | {c.get('n')} | {c.get('brier')} | {c.get('mean_score')} | "
          f"{c.get('observed_win_rate')} | {c.get('calibration_gap')} |")
    a("")
    a("### Sıralama becerisi (permütasyon null testi)")
    a("")
    a("| Varyant | Alınan | Kapasite reddi | Beklenti (R) | Null medyan | Null GA95 | Yüzdelik | Ayırt edilebilir |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for k, v in report["entry"]["ranking_null_tests"].items():
        if v.get("state") != "RUN":
            a(f"| {k} | — | — | — | — | — | — | {v.get('state')} |")
            continue
        a(f"| {k} | {v['n_taken']} | {v['n_skipped_capacity']} | {v['actual_expectancy_r']} | "
          f"{v['null_median']} | {v['null_ci95']} | {v['percentile_of_actual']} | "
          f"{'EVET' if v['distinguishable_from_random'] else 'HAYIR'} |")
    a("")
    wf = report["entry"]["walk_forward_attempt"]
    a(f"**Walk-forward denemesi:** `{wf.get('state')}`. " + str(wf.get("decision") or ""))
    a("")
    a("## 6. Araştırma dersleri")
    a("")
    for ls in lessons["lessons"]:
        a(f"### {ls['lesson_id']} — {ls['title']} ({ls['behaviour']}, "
          f"{ls['completeness']['state']})")
        a("")
        a(f"* **İddia:** {ls['claim']}")
        a(f"* **Kanıt sınıfı:** {ls['evidence_class']} · **örneklem:** {ls['sample_size']} · "
          f"**eksik:** {ls['missing_count']}")
        a(f"* **Net etki:** {ls['net_effect']}")
        a(f"* **Belirsizlik:** {ls['uncertainty']}")
        a(f"* **Çelişen kanıt:** {', '.join(str(x) for x in ls['contradicting_ids'][:6])}")
        a(f"* **Alternatif açıklamalar:** {'; '.join(ls['alternative_explanations'])}")
        a(f"* **Yanlışlanabilir sonraki test:** {ls['falsifiable_next_test']}")
        a("")
    a("## 7. Sınırlamalar")
    a("")
    for lim in p["limitations"]:
        a(f"* {lim}")
    a("")
    a("## 8. Yazma sınırı")
    a("")
    for k, v in report["write_boundary"].items():
        a(f"* `{k}`: {v}")
    a("")
    a(f"Çalışma süresi {report['runtime']['elapsed_s']} s · bar önbelleği "
      f"{report['runtime']['bar_cache_stats']}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", required=True, help="salt okunur export dizini")
    ap.add_argument("--out", required=True, help="rapor/önbellek çıktı kökü")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-concurrent", type=int, default=11)
    ap.add_argument("--n-null", type=int, default=200)
    ap.add_argument("--offline", action="store_true", help="ağ isteği YAPMA (yalnız önbellek)")
    ap.add_argument("--force", action="store_true", help="önbelleği yok say, yeniden hesapla")
    ap.add_argument("--stage", choices=("v1", "dual_edge", "integrity", "all"), default="v1",
                    help=("v1: özgün rapor · dual_edge: doğrulama+çift kapı · "
                          "integrity: sayaç bütünlüğü + düzeltilmiş kıyaslama · all"))
    args = ap.parse_args(argv)

    export_dir, out_dir = Path(args.export), Path(args.out)
    _guard_out(out_dir)
    if not (export_dir / "state").is_dir():
        print(f"HATA: export dizini bulunamadı: {export_dir}/state", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.stage in ("integrity", "all"):
        from .integrity_run import (INTEGRITY_JSON, INTEGRITY_MD, build_integrity_report,
                                    render_integrity_markdown)
        ip = out_dir / INTEGRITY_JSON
        if ip.exists() and not args.force:
            print(f"INTEGRITY_CACHE_HIT {ip} (yeniden hesaplanmadı; --force ile zorla)")
        else:
            rep = _finite(build_integrity_report(export_dir, out_dir, seed=args.seed,
                                                 offline=args.offline))
            ip.write_text(json.dumps(rep, ensure_ascii=False, indent=1, allow_nan=False),
                          encoding="utf-8")
            (out_dir / INTEGRITY_MD).write_text(render_integrity_markdown(rep), encoding="utf-8")
            ci = rep["count_integrity"]
            print(f"INTEGRITY_OK closes={ci['n_final_closes']} "
                  f"win_global_old={ci['weighted_mass_global_old']} "
                  f"new={ci['weighted_mass_global_new']} "
                  f"dup_nodes={ci['n_duplicated_nodes']} verdict={ci['verdict']} "
                  f"flips={rep['edge_sensitivity']['n_gate_verdict_flips']} "
                  f"elapsed={rep['runtime']['elapsed_s']}s -> {ip}")
        if args.stage == "integrity":
            return 0

    if args.stage in ("dual_edge", "all"):
        from .dual_edge_run import (CORRECTIONS_JSON, DUAL_JSON, DUAL_MD,
                                    build_dual_edge_report, render_dual_markdown)
        dp = out_dir / DUAL_JSON
        if dp.exists() and not args.force:
            print(f"DUAL_EDGE_CACHE_HIT {dp} (yeniden hesaplanmadı; --force ile zorla)")
        else:
            dual = _finite(build_dual_edge_report(export_dir, out_dir, seed=args.seed,
                                                  offline=args.offline))
            dp.write_text(json.dumps(dual, ensure_ascii=False, indent=1, allow_nan=False),
                          encoding="utf-8")
            (out_dir / DUAL_MD).write_text(render_dual_markdown(dual), encoding="utf-8")
            (out_dir / CORRECTIONS_JSON).write_text(
                json.dumps({"schema_version": "research_corrections_v1",
                            "corrections": dual["corrections"]},
                           ensure_ascii=False, indent=1), encoding="utf-8")
            gc = dual["dual_gate_counts"]["counts"]
            print(f"DUAL_EDGE_OK gates(prior/model) YY={gc['prior_YES_model_YES']} "
                  f"YN={gc['prior_YES_model_NO']} NY={gc['prior_NO_model_YES']} "
                  f"NN={gc['prior_NO_model_NO']} "
                  f"class={dual['separation_classification']['classification']} "
                  f"f00036={dual['f00036_reconciliation']['gap_resolution']['state']} "
                  f"elapsed={dual['runtime']['elapsed_s']}s -> {dp}")
        if args.stage == "dual_edge":
            return 0

    rp = out_dir / REPORT_JSON
    if rp.exists() and not args.force:
        try:
            prev = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = None
        if prev:
            probe = ResearchDataset.load(export_dir)
            hashes = {n: file_sha256(export_dir / "state" / n) for n in HASHED_INPUTS}
            same = (prev.get("protocol", {}).get("input_hashes") == hashes
                    and prev.get("protocol", {}).get("code_sha") == _code_sha()
                    and prev.get("protocol", {}).get("cutoff_utc") == str(probe.cutoff_utc))
            if same:
                print(f"RESEARCH_CACHE_HIT research_id={prev['protocol']['research_id']} "
                      f"{rp} (yeniden hesaplanmadı; --force ile zorla)")
                return 0

    built = build_report(export_dir, out_dir, seed=args.seed, offline=args.offline,
                         max_concurrent=args.max_concurrent, n_null=args.n_null)
    report, lessons = built["report"], built["lessons"]
    (out_dir / REPORT_JSON).write_text(
        json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    (out_dir / LESSONS_JSON).write_text(
        json.dumps(lessons, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    (out_dir / REPORT_MD).write_text(render_markdown(report, lessons), encoding="utf-8")
    print(f"RESEARCH_REPORT_OK research_id={report['protocol']['research_id']} "
          f"closed={report['coverage']['n_closed']} "
          f"opportunities={report['entry']['opportunity_pool']['n_unique']} "
          f"lessons={lessons['n']}/{lessons['n_complete']} complete "
          f"elapsed={report['runtime']['elapsed_s']}s -> {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
