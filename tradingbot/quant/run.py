"""Offline Quant Evaluation rapor üretici (`quant_eval_v1`) — worker'dan TAMAMEN bağımsız.

Kullanım (yalnız operatör elle çalıştırır; hot loop'ta ÇAĞRILMAZ):

    python -m tradingbot.quant.run --memory state/trade_memory.jsonl \
        --shadow state/shadow_book.json --out reports/quant_eval.json

Girdiler SALT OKUNUR açılır; tek yazım `--out` yoludur (atomic). Ağ erişimi, API anahtarı,
emir/outbox/ledger yolu YOKTUR. Dashboard `/quant` görünümü bu dosyayı state dizininden okur —
operatör isterse çıktıyı oraya kendisi kopyalar/işaret eder; bu araç canlı state'e yazmaz
(`--out` state dizinini gösteriyorsa açık `--allow-state-out` bayrağı gerekir).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..core import atomic_write_json, read_json
from .attribution import attribution_v1, group_metrics
from .champion import evaluate_challenger
from .journal import unify
from .manifest import build_manifest

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


def build_report(*, memory_rows: list[dict], shadow_trades: list[dict], run_id: str,
                 code_sha: str, seed: int = 7, min_sample: int = 10,
                 config_obj: object | None = None) -> dict:
    """Deterministik rapor: journal + attribution + (varsayılan KEEP_CHAMPION) + manifest."""
    rows = unify(memory_rows, shadow_trades)
    labeled = [r for r in rows if r.get("outcome_labeled")]
    attribution = attribution_v1(labeled, min_sample=min_sample, seed=seed)
    overall = attribution["overall_real"]
    # Bu araç tek başına challenger verisi taşımaz → değerlendirme kanıtsızdır ve varsayılan
    # KEEP_CHAMPION döner; PROMOTE bu yoldan ÇIKAMAZ (kanıtlar policy_eval akışından gelir).
    cc = evaluate_challenger(overall, overall)
    warnings: list[str] = []
    if overall.get("insufficient_sample"):
        warnings.append(f"genel havuz yetersiz örnek (n={overall.get('n')}, eşik {min_sample})")
    n_flagged = sum(1 for r in rows if r.get("quality_flags"))
    if n_flagged:
        warnings.append(f"{n_flagged} kayıtta veri kalitesi bayrağı var")
    manifest = build_manifest(
        run_id=run_id, code_sha=code_sha, config_obj=config_obj, seed=seed,
        feature_schema_version="quant_journal_v1",
        result_obj={"n_records": len(rows), "n_labeled": len(labeled)},
        data_quality={"passed": None, "checks": [],
                      "note": "journal özet raporu — backtest kalite kapısı bu araçta çalışmaz"})
    return {"schema_version": SCHEMA_VERSION,
            "journal": {"n_records": len(rows), "n_labeled": len(labeled),
                        "n_accepted": sum(1 for r in rows if r.get("accepted"))},
            "overall": overall,
            "attribution_summary": {dim: groups for dim, groups in
                                    attribution["by_dimension_real"].items()
                                    if dim in ("symbol", "direction", "regime", "exit_reason")},
            "champion_challenger": cc,
            "walk_forward": None,
            "risk_clusters": None,
            "manifest": manifest,
            "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quant Evaluation V1 offline rapor üretici (read-only)")
    ap.add_argument("--memory", required=True, help="TradeMemory JSONL yolu (salt okunur)")
    ap.add_argument("--shadow", required=True, help="shadow_book.json yolu (salt okunur)")
    ap.add_argument("--out", required=True, help="çıktı JSON yolu (atomic yazılır)")
    ap.add_argument("--run-id", default="quant-eval-manual")
    ap.add_argument("--code-sha", default="unknown")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-sample", type=int, default=10)
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
    report = build_report(memory_rows=memory_rows, shadow_trades=list(shadow_doc.get("trades", [])),
                          run_id=args.run_id, code_sha=args.code_sha, seed=args.seed,
                          min_sample=args.min_sample)
    atomic_write_json(out, report)
    print(f"QUANT_EVAL_REPORT_OK {out} (records={report['journal']['n_records']}, "
          f"labeled={report['journal']['n_labeled']})")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
