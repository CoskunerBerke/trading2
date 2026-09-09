"""Onceden kayit altina alinan C1/C2/C3 rakiplerini olcer. `docs/CHALLENGERS_V1.md` ONCE islendi.

Temel ve rakipler AYNI `run_plan` yolundan, AYNI mumlarla, AYNI defterle gecer; tek fark `Policy`.
C1/C2 icin olcum ESLESTIRILMIS farktir (gun etkisi tam olarak sadelesir); C3 icin gun-ortalamasi
cikarilmis R uzerinde alt kume farkidir.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.core import D  # noqa: E402
from tradingbot.replay.challengers import BASELINE, C1, C2, plan_bars_window, run_plan  # noqa: E402
from tradingbot.replay.counterfactual import SymbolWindow, assign_episodes, iter_candidates  # noqa: E402
from tradingbot.replay.fidelity import BinanceBars, replay_config_from_ledger  # noqa: E402

POLICIES = [BASELINE, C1, C2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="data/cf_bars")
    ap.add_argument("--horizon", type=float, default=72.0)
    ap.add_argument("--data-end-ms", type=int, required=True)
    ap.add_argument("--be-mfe-r", default=None, help="EXPLORATORY duyarlilik; bos birakilirsa defter degeri (1.0)")
    args = ap.parse_args()

    snap = Path(args.snapshot)
    cfg = replay_config_from_ledger(json.loads((snap / "futures_ledger.json").read_text(encoding="utf-8")))
    src = BinanceBars(args.cache)
    be = D(args.be_mfe_r) if args.be_mfe_r is not None else None
    cands = list(iter_candidates(snap / "entry_snapshot.jsonl"))
    win_start = min(c.ts_ms for c in cands) - 60 * 60_000
    horizon_ms = int(args.horizon * 3_600_000)

    by_sym: dict[str, list] = {}
    for c in cands:
        by_sym.setdefault(c.symbol, []).append(c)

    rows: list[dict] = []
    t0 = time.time()
    for i, (sym, group) in enumerate(sorted(by_sym.items())):
        end = min(args.data_end_ms, max(c.ts_ms for c in group) + horizon_ms) + 60_000
        win = SymbolWindow(src, sym, win_start, end)
        lookup = win.funding_lookup()
        for c in group:
            s_ms, e_ms = plan_bars_window(c.dt, args.horizon)
            bars = win.slice(s_ms, e_ms)
            row = {
                "candidate_id": c.candidate_id, "ts": c.ts, "ts_ms": c.ts_ms, "symbol": c.symbol,
                "direction": c.direction, "regime": c.regime, "setup": c.setup, "accepted": c.accepted,
                "reject_reason": c.reject_reason, "rank": c.rank, "p_win": c.p_win,
                "conservative_net_edge_r": c.conservative_net_edge_r, "expected_r": c.expected_r,
                "net_expectancy_r": c.net_expectancy_r, "expected_cost_pct": c.expected_cost_pct,
                "stop_distance_pct": c.stop_distance_pct, "planned_notional": c.planned_notional,
                "planned_leverage": c.planned_leverage,
                "mature": c.ts_ms + horizon_ms <= args.data_end_ms,
            }
            for pol in POLICIES:
                o = run_plan(symbol=c.symbol, side=c.direction, ref_entry=D(str(c.entry_price)),
                             stop=D(str(c.stop_price)), targets=c.targets,
                             notional=D(str(c.planned_notional)), leverage=int(c.planned_leverage or 1),
                             amount_type="NOTIONAL", opened_at=c.dt, bars=bars, cfg=cfg, policy=pol,
                             funding_lookup=lookup, be_mfe_r=be)
                row[pol.name] = {"r": o.r_multiple, "exit": o.exit_reason, "mfe_r": o.mfe_r,
                                 "minutes": o.minutes_held, "excluded": o.excluded, "note": o.note,
                                 "time_boxed": o.time_boxed, "amb": o.ambiguous_bars}
            rows.append(row)
        print(f"[{i+1}/{len(by_sym)}] {sym:14} n={len(group):<4} {time.time()-t0:6.1f}s", flush=True)
        del win
    assign_episodes(rows, block_h=args.horizon)
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({"horizon_h": args.horizon, "data_end_ms": args.data_end_ms,
                                "be_mfe_r": args.be_mfe_r, "policies": [p.name for p in POLICIES],
                                "rows": rows}), encoding="utf-8")
    print(f"\nyazildi {outp} satir={len(rows)} sure={time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
