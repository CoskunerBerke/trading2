"""Butun `entry_snapshot` adaylarini gercek 1m mumlarla izole olarak yeniden kosar.

Kullanim:
    python scripts/counterfactual_run.py --snapshot <dizin> --horizon 72 --out data/cf_72h.json

Cikti: aday basina karar ani alanlari + ileri sonuc (R, cikis nedeni, MFE/MAE, maliyet, olgunluk).
Sonuc alanlari KARAR ANI alanlarindan ayri tutulur; hicbir sonuc aday secimine geri beslenmez.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.core import D  # noqa: E402
from tradingbot.replay.counterfactual import assign_episodes, iter_candidates, run_all  # noqa: E402
from tradingbot.replay.fidelity import BinanceBars, replay_config_from_ledger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="data/cf_bars")
    ap.add_argument("--horizon", type=float, default=72.0)
    ap.add_argument("--data-end-ms", type=int, default=None, help="mevcut verinin sonu; yoksa fapi/time")
    ap.add_argument("--be-mfe-r", default=None, help="basa-bas tetigi (R). Yoksa defter degeri.")
    ap.add_argument("--limit-symbols", type=int, default=0, help="pilot kosum icin sembol sayisi")
    args = ap.parse_args()

    snap = Path(args.snapshot)
    ledger = json.loads((snap / "futures_ledger.json").read_text(encoding="utf-8"))
    cfg = replay_config_from_ledger(ledger)
    src = BinanceBars(args.cache)

    if args.data_end_ms is None:
        args.data_end_ms = int(src.http.get("/fapi/v1/time")["serverTime"])
    cands = list(iter_candidates(snap / "entry_snapshot.jsonl"))
    if args.limit_symbols:
        keep = sorted({c.symbol for c in cands})[: args.limit_symbols]
        cands = [c for c in cands if c.symbol in keep]
    win_start = min(c.ts_ms for c in cands) - 60 * 60_000
    print(f"adaylar: {len(cands)}  sembol: {len({c.symbol for c in cands})}  ufuk: {args.horizon}h "
          f"veri sonu: {args.data_end_ms}", flush=True)

    t0 = time.time()

    def prog(i, n, sym, k, nb):
        print(f"[{i}/{n}] {sym:14} adaylar={k:<4} mum={nb:<6} gecen={time.time()-t0:6.1f}s "
              f"http={src.stats}", flush=True)

    rows = run_all(cands, src, cfg, horizon_h=args.horizon, data_end_ms=args.data_end_ms,
                   window_start_ms=win_start, be_mfe_r=D(args.be_mfe_r) if args.be_mfe_r is not None else None,
                   progress=prog)
    assign_episodes(rows)
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({"horizon_h": args.horizon, "data_end_ms": args.data_end_ms,
                                "be_mfe_r": args.be_mfe_r, "http": src.stats, "rows": rows}), encoding="utf-8")
    mature = [r for r in rows if r.get("mature") and r.get("r_multiple") is not None]
    print(f"\nyazildi {outp}  satir={len(rows)} olgun={len(mature)} "
          f"bolum={len({r['episode'] for r in mature})} sure={time.time()-t0:.1f}s http={src.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
