"""Golge akisi uretir: aday basina bir JSONL satiri, kabul+ret, temel ve rakipler yan yana.

Kullanim:
    python scripts/shadow_stream.py --snapshot <dizin> --eval data/chal_72h.json \
        --out research_out/shadow_stream_72h.jsonl

Cikti yolu `assert_safe_output` kapisindan gecer: uretim durum dosyalarina yazmaya calisirsa
KOSUM DUSER. Benzetilmis sonuclar gercek kapanmis islemlerin ogrenme kayitlarina karismaz.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.replay.challengers import C3_FIELD, C3_TAU  # noqa: E402
from tradingbot.replay.shadow import assert_safe_output, iter_shadow  # noqa: E402


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with io.open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    outp = assert_safe_output(args.out)
    snap = Path(args.snapshot)
    ev = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    rows = {str(r["candidate_id"]): r for r in ev["rows"]}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[1]).stdout.strip()
    except Exception:                                                # noqa: BLE001
        head = "unknown"
    prov = {
        "base_commit": head,
        "snapshot_futures_ledger_sha256_16": sha16(snap / "futures_ledger.json"),
        "snapshot_entry_snapshot_sha256_16": sha16(snap / "entry_snapshot.jsonl"),
        "snapshot_config_sha256_16": sha16(snap / "config.yaml"),
        "bar_source": "binance fapi /fapi/v1/klines 1m + /fapi/v1/fundingRate",
        "accounting": "tradingbot.accounting.FuturesLedgerV2 via replay_config_from_ledger",
        "intrabar_order": "liquidation -> STOP -> targets (pessimistic, production order)",
        "harness": ["tradingbot/replay/counterfactual.py", "tradingbot/replay/challengers.py"],
        "pre_registration": "docs/CHALLENGERS_V1.md",
        "simulated_only": True,
        "must_not_be_written_into": "any production learning record of a real closed trade",
    }

    n = mature = accepted = 0
    outp.parent.mkdir(parents=True, exist_ok=True)
    with io.open(outp, "w", encoding="utf-8") as fh:
        for rec in iter_shadow(snap / "entry_snapshot.jsonl", rows, horizon_h=float(ev["horizon_h"]),
                               data_end_ms=int(ev["data_end_ms"]), c3_field=C3_FIELD, c3_tau=C3_TAU,
                               provenance=prov):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            mature += bool(rec["maturity"]["mature"])
            accepted += bool(rec["decision"]["accepted"])
    print(f"yazildi {outp}\n  kayit={n} olgun={mature} kabul={accepted} ret={n - accepted}")
    print("  ad alani=research_shadow  semam=research_shadow_v1  (uretim okuyuculari bu ad alanini tanimaz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
