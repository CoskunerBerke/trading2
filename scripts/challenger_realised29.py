"""A6 — C1/C2'yi defterin GERCEK 29 kapanmis islemi uzerinde kosar (havuz disi ornek).

Bu 29 islem aday penceresinden (2026-09-02..09) ONCE acildi; entry_snapshot onlari icermez. Bu
yuzden bagimsiz bir kontroldur. Ayni `run_plan` yolu, ayni defter, ayni kotumser bar ici siralama.
Iki ufuk birden raporlanir: havuzla tutarli (giristen 72h) ve sadakat kosumuyla tutarli
(kayitli kapanis + 72h).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.replay.challengers import BASELINE, C1, C2, plan_bars_window, run_trade_plan  # noqa: E402
from tradingbot.replay.fidelity import (  # noqa: E402
    BinanceBars, funding_lookup_from, load_plans, path_targets_index, replay_config_from_ledger,
)
from tradingbot.replay.stats import cluster_bootstrap, mean  # noqa: E402

POLICIES = [BASELINE, C1, C2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="data/replay_bars")
    ap.add_argument("--horizon", type=float, default=72.0)
    args = ap.parse_args()

    snap = Path(args.snapshot)
    ledger = json.loads((snap / "futures_ledger.json").read_text(encoding="utf-8"))
    cfg = replay_config_from_ledger(ledger)
    targets = {}
    if (snap / "position_path.jsonl").exists():
        with io.open(snap / "position_path.jsonl", encoding="utf-8") as fh:
            targets = path_targets_index(fh)
    plans = load_plans(ledger, targets)
    src = BinanceBars(args.cache)

    rows = []
    for p in plans:
        s_ms, e_ms = plan_bars_window(p.opened_at, args.horizon)
        e_nat = int((p.closed_at.timestamp() + args.horizon * 3600) * 1000)
        bars_pool = src.bars(p.symbol, "1m", s_ms, e_ms)
        bars_nat = src.bars(p.symbol, "1m", s_ms, e_nat)
        rates = src.funding_rates(p.symbol, s_ms - 8 * 3_600_000, max(e_ms, e_nat))
        lk = funding_lookup_from(rates) if rates else None
        row = {"trade_id": p.trade_id, "symbol": p.symbol, "side": p.side,
               "opened_at": p.opened_at.isoformat(), "hold_h": round(p.hold_hours, 2),
               "recorded_r": float(p.rec_r_multiple), "recorded_exit": p.rec_exit_reason}
        for pol in POLICIES:
            a = run_trade_plan(p, bars_pool, cfg, pol, funding_lookup=lk)
            b = run_trade_plan(p, bars_nat, cfg, pol, funding_lookup=lk)
            row[pol.name] = {"pool72_r": a.r_multiple, "pool72_exit": a.exit_reason,
                             "natural_r": b.r_multiple, "natural_exit": b.exit_reason,
                             "time_boxed": a.time_boxed, "excluded": a.excluded}
        rows.append(row)
        print(f"{p.trade_id} {p.symbol:12} rec={float(p.rec_r_multiple):+.3f} "
              + "  ".join(f"{pol.name.split('_')[0]}={row[pol.name]['pool72_r']}" for pol in POLICIES), flush=True)

    out = {"n": len(rows), "rows": rows}
    for pol in (C1, C2):
        for key in ("pool72_r", "natural_r"):
            pairs = [{"d": r[pol.name][key] - r[BASELINE.name][key], "symbol": r["symbol"]}
                     for r in rows if r[pol.name][key] is not None and r[BASELINE.name][key] is not None]
            b = cluster_bootstrap(pairs, lambda x: x["d"], lambda x: x["symbol"], n_boot=5000)
            out.setdefault(pol.name, {})[key] = {
                "mean_delta_r": b["point"], "lo": b["lo"], "hi": b["hi"], "n": b["n"],
                "n_clusters": b["n_clusters"], "underpowered": b["underpowered"],
                "mean_r_baseline": mean([r[BASELINE.name][key] for r in rows if r[BASELINE.name][key] is not None]),
                "mean_r_challenger": mean([r[pol.name][key] for r in rows if r[pol.name][key] is not None]),
            }
        out[pol.name]["mean_delta_r"] = out[pol.name]["pool72_r"]["mean_delta_r"]
    out[BASELINE.name] = {
        "mean_r_pool72": mean([r[BASELINE.name]["pool72_r"] for r in rows if r[BASELINE.name]["pool72_r"] is not None]),
        "mean_r_natural": mean([r[BASELINE.name]["natural_r"] for r in rows if r[BASELINE.name]["natural_r"] is not None]),
        "mean_r_recorded": mean([r["recorded_r"] for r in rows]),
    }
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\n" + json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
