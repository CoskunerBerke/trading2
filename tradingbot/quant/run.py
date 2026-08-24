"""Offline Quant Evaluation rapor üretici (`quant_eval_v1`) — worker'dan TAMAMEN bağımsız.

Kullanım (yalnız operatör elle çalıştırır; hot loop'ta ÇAĞRILMAZ):

    python -m tradingbot.quant.run --memory state/trade_memory.jsonl \
        --shadow state/shadow_book.json --out reports/quant_eval.json

İsteğe bağlı girdiler (hepsi SALT OKUNUR, üretim şemasıyla uyumlu):

    --ledger state/futures_ledger.json     Risk V2 offline raporu için açık pozisyonlar
    --returns returns.json                 {"SYM": [getiri, ...]} korelasyon/volatilite için
    --evidence evidence.json               walk-forward/policy_eval kanıt paketi girdisi
    --eligibility elig_*.json              point-in-time uygunluk artifact'leri

Girdiler açılırken hiçbir dosyaya yazılmaz; tek yazım `--out` yoludur (atomic). Ağ erişimi, API
anahtarı, emir/outbox/ledger yazımı YOKTUR. `--out` bir `state/` dizinini gösteriyorsa açık
`--allow-state-out` bayrağı gerekir.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, read_json
from .attribution import attribution_v1
from .coverage import CoverageGates, journal_coverage
from .eligibility import load_store
from .evidence import build_evidence, evaluate_with_evidence
from .execution_scenarios import compare_scenarios
from .journal import unify
from .manifest import build_manifest
from .risk_v2 import RiskV2Config, offline_risk_report, positions_from_ledger

SCHEMA_VERSION = "quant_eval_v1"


def _read_memory_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def trades_for_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Journal kayıtlarından senaryo motorunun beklediği işlem listesini türetir.

    `risk_usdt` yalnız `net_pnl` ve `r_multiple` birlikte varsa türetilir (|net/R|); türetilemeyen
    işlemde alan `None` bırakılır — uydurulmaz, senaryo motoru onu R hesabından hariç tutar.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("outcome_labeled") or r.get("is_counterfactual"):
            continue
        gross, net = _f(r.get("gross_pnl")), _f(r.get("net_pnl"))
        rm = _f(r.get("r_multiple"))
        # Gerçekleşen notional önceliklidir; yoksa planlanan kullanılır. (Replay TradeMemory
        # girişlerinde `plan.notional` bulunmaz — yalnız plana bakmak bütün işlemleri atlıyordu.)
        notional = _f(r.get("effective_notional")) or _f(r.get("planned_notional"))
        if gross is None and net is not None:
            gross = net + (_f(r.get("fees")) or 0.0) - (_f(r.get("funding")) or 0.0)
        if gross is None or notional is None or notional <= 0:
            continue
        risk = abs(net / rm) if (net is not None and rm not in (None, 0.0)) else None
        out.append({"symbol": r.get("symbol"), "gross_pnl": gross, "notional": notional,
                    "risk_usdt": risk, "fees": _f(r.get("fees")) or 0.0,
                    "funding": _f(r.get("funding")) or 0.0,
                    "volatility_pct": _f((r.get("feature_snapshot") or {}).get("atr_pct")),
                    "bar_quote_volume": _f((r.get("feature_snapshot") or {}).get("quote_volume")),
                    "gap": str(r.get("exit_reason") or "").lower().find("gap") >= 0})
    return out


def _with_shadow_archive(shadow_path: Path, active: list[dict], *,
                         archive_dir: str | None, enabled: bool) -> list[dict]:
    """Aktif gölge kayıtlarına ARŞİVLENMİŞ geçmişi ekler (`id` ile tekilleştirilmiş).

    Offline rapor ömür boyu kaydı görmelidir; aktif dosya sınırlıdır. Arşiv yoksa ya da
    okunamıyorsa rapor AKTİF kayıtlarla üretilir (sessizce boş dönmez, sayı raporlanır).
    """
    if not enabled:
        return list(active)
    root = Path(archive_dir) if archive_dir else shadow_path.parent / "shadow_archive"
    if not (root / "manifest.json").exists():
        return list(active)
    try:
        from ..learn.journal_archive import SegmentArchive
        arc = SegmentArchive(root, stream_id="shadow_book",
                             record_schema_version="shadow_trade_v1")
        seen = {str(t.get("id")) for t in active if t.get("id")}
        merged: list[dict] = []
        for row in arc.iter_rows():
            sid = str(row.get("id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            merged.append(row)
        merged.extend(active)
        return merged
    except Exception as exc:  # noqa: BLE001 — arşiv arızası raporu ÇÖKERTMEZ
        print(f"UYARI: gölge arşivi okunamadı ({exc}); rapor yalnız aktif kayıtlarla üretildi.",
              file=sys.stderr)
        return list(active)


def build_report(*, memory_rows: list[dict], shadow_trades: list[dict], run_id: str,
                 code_sha: str, seed: int = 7, min_sample: int = 10,
                 config_obj: object | None = None,
                 ledger_doc: dict | None = None,
                 returns_by_symbol: dict[str, list[float]] | None = None,
                 evidence_input: dict | None = None,
                 eligibility_paths: list[Path] | None = None,
                 coverage_gates: CoverageGates | None = None,
                 now_ms: int | None = None) -> dict:
    """Deterministik rapor: journal → coverage → attribution → senaryolar → risk V2 → kanıt köprüsü.

    Champion/challenger kararı KÖPRÜ üzerinden verilir: geçerli walk-forward/policy_eval kanıtı
    sağlanmışsa gerçekten değerlendirilir, sağlanmamışsa kritik kanıt eksik olduğu için
    `KEEP_CHAMPION` döner (fail-closed).
    """
    rows = unify(memory_rows, shadow_trades)
    labeled = [r for r in rows if r.get("outcome_labeled")]
    coverage = journal_coverage(rows, gates=coverage_gates, now_ms=now_ms)
    attribution = attribution_v1(labeled, min_sample=min_sample, seed=seed)
    overall = attribution["overall_real"]
    scenarios = compare_scenarios(trades_for_scenarios(labeled))

    risk_report = None
    if ledger_doc is not None:
        risk_report = offline_risk_report(positions_from_ledger(ledger_doc),
                                          returns_by_symbol or {},
                                          cfg=RiskV2Config(), now_ms=now_ms)

    eligibility = None
    if eligibility_paths:
        store = load_store(eligibility_paths)
        eligibility = {"n_snapshots": len(store), "symbols": store.symbols,
                       "status": "OK" if len(store) else "UNAVAILABLE",
                       "note": "point-in-time artifact yüklendi; replay kapsaması ayrı ölçülür"}

    ev_in = dict(evidence_input or {})
    evidence = build_evidence(
        champion_metrics=ev_in.get("champion_metrics", overall),
        challenger_metrics=ev_in.get("challenger_metrics"),
        walk_forward=ev_in.get("walk_forward"),
        policy_eval=ev_in.get("policy_eval"),
        leakage=ev_in.get("leakage"),
        data_quality=ev_in.get("data_quality"),
        isolation=ev_in.get("isolation"),
        cost_model_equal=ev_in.get("cost_model_equal"),
        execution_quality=ev_in.get("execution_quality"),
        scenarios=scenarios,
        coverage=coverage)
    cc = evaluate_with_evidence(evidence)

    warnings: list[str] = list(coverage.get("warnings") or [])
    if overall.get("insufficient_sample"):
        warnings.append(f"genel havuz yetersiz örnek (n={overall.get('n')}, eşik {min_sample})")
    n_flagged = sum(1 for r in rows if r.get("quality_flags"))
    if n_flagged:
        warnings.append(f"{n_flagged} kayıtta veri kalitesi bayrağı var")
    if scenarios.get("robust_across_scenarios") is False:
        warnings.append(f"senaryo kırılganlığı: {scenarios.get('verdict')}")
    if eligibility is None:
        warnings.append("point-in-time eligibility artifact verilmedi — "
                        "backtest geçerliliği PARTIAL kabul edilir")

    dq_passed = (ev_in.get("data_quality") or {}).get("passed")
    manifest = build_manifest(
        run_id=run_id, code_sha=code_sha, config_obj=config_obj, seed=seed,
        feature_schema_version="quant_journal_v1",
        result_obj={"n_records": len(rows), "n_labeled": len(labeled),
                    "expectancy_r": overall.get("expectancy_r"),
                    "scenarios": scenarios.get("expectancy_r_by_scenario")},
        data_quality={"passed": dq_passed, "checks": (ev_in.get("data_quality") or {}).get("checks", []),
                      "note": ("journal özet raporu — backtest kalite kapısı bu araçta çalışmaz"
                               if dq_passed is None else "kanıt paketinden alındı")})
    backtest_status = "VALID" if manifest.get("valid_backtest") else (
        "PARTIAL" if eligibility or dq_passed is not None else "PARTIAL")
    return {"schema_version": SCHEMA_VERSION,
            "data_kind": "LIVE_PAPER_JOURNAL",
            "backtest_status": backtest_status,
            "journal": {"n_records": len(rows), "n_labeled": len(labeled),
                        "n_accepted": sum(1 for r in rows if r.get("accepted"))},
            "coverage": coverage,
            "overall": overall,
            "attribution_summary": {dim: groups for dim, groups in
                                    attribution["by_dimension_real"].items()
                                    if dim in ("symbol", "direction", "regime", "exit_reason")},
            "execution_scenarios": scenarios,
            "risk_v2": risk_report,
            "eligibility": eligibility,
            "evidence": evidence,
            "champion_challenger": cc,
            "walk_forward": ev_in.get("walk_forward"),
            "risk_clusters": (risk_report or {}).get("exposure"),
            "manifest": manifest,
            "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quant Evaluation V1 offline rapor üretici (read-only)")
    ap.add_argument("--memory", required=True, help="TradeMemory JSONL yolu (salt okunur)")
    ap.add_argument("--shadow", required=True, help="shadow_book.json yolu (salt okunur)")
    ap.add_argument("--shadow-archive", default=None,
                    help="gölge arşiv kökü (varsayılan: shadow_book.json yanındaki shadow_archive/)")
    ap.add_argument("--no-archive", action="store_true",
                    help="arşivlenmiş geçmişi rapora KATMA (yalnız aktif dosya)")
    ap.add_argument("--out", required=True, help="çıktı JSON yolu (atomic yazılır)")
    ap.add_argument("--ledger", default=None, help="futures_ledger.json (Risk V2 offline raporu)")
    ap.add_argument("--returns", default=None, help='{"SYM": [getiri,...]} JSON yolu')
    ap.add_argument("--evidence", default=None, help="walk-forward/policy_eval kanıt paketi JSON")
    ap.add_argument("--eligibility", nargs="*", default=None, help="eligibility artifact yolları")
    ap.add_argument("--run-id", default="quant-eval-manual")
    ap.add_argument("--code-sha", default="unknown")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-sample", type=int, default=10)
    ap.add_argument("--now-ms", type=int, default=None, help="veri yaşı hesabı için sabit an")
    ap.add_argument("--allow-state-out", action="store_true",
                    help="çıktının bir state/ dizinine yazılmasına açıkça izin ver")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if "state" in out.parts and not args.allow_state_out:
        print("HATA: --out bir state/ dizinini gösteriyor; canlı state'e yazım için "
              "--allow-state-out bayrağını açıkça vermelisin.", file=sys.stderr)
        return 2
    memory_rows = _read_memory_jsonl(Path(args.memory))
    shadow_doc = read_json(Path(args.shadow), default={"trades": []}) or {"trades": []}
    shadow_trades = _with_shadow_archive(Path(args.shadow), list(shadow_doc.get("trades", [])),
                                         archive_dir=args.shadow_archive,
                                         enabled=not args.no_archive)
    ledger_doc = read_json(Path(args.ledger), default=None) if args.ledger else None
    returns = read_json(Path(args.returns), default=None) if args.returns else None
    evidence_input = read_json(Path(args.evidence), default=None) if args.evidence else None
    elig = [Path(p) for p in (args.eligibility or [])]
    report = build_report(memory_rows=memory_rows, shadow_trades=shadow_trades,
                          run_id=args.run_id, code_sha=args.code_sha, seed=args.seed,
                          min_sample=args.min_sample, ledger_doc=ledger_doc,
                          returns_by_symbol=returns, evidence_input=evidence_input,
                          eligibility_paths=elig or None, now_ms=args.now_ms)
    atomic_write_json(out, report)
    print(f"QUANT_EVAL_REPORT_OK {out} (records={report['journal']['n_records']}, "
          f"labeled={report['journal']['n_labeled']}, "
          f"decision={report['champion_challenger']['decision']}, "
          f"backtest={report['backtest_status']})")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
