"""Replay sadakat kosumunu calistirir: defterin kapanmis islemlerini gercek `fapi` mumlariyla yeniden uretir.

Kullanim:
    python scripts/replay_fidelity.py --snapshot <dizin> [--out data/replay_fidelity_rows.json]
        [--cache data/replay_bars] [--interval 1m --interval 1h] [--horizon 72]

`--snapshot` icinde `futures_ledger.json` (zorunlu) ve varsa `position_path.jsonl` aranir.
Cikti: her (islem × cozunurluk × mark modu) icin bir satir + toplu ozet.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.core import from_iso  # noqa: E402
from tradingbot.replay.fidelity import (  # noqa: E402
    BinanceBars, compare, load_plans, path_targets_index, replay_config_from_ledger, replay_trade, summarise,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay sadakat kosumu")
    ap.add_argument("--snapshot", required=True, help="uretim anlik goruntusu dizini (SALT OKUNUR)")
    ap.add_argument("--out", default="data/replay_fidelity_rows.json")
    ap.add_argument("--cache", default="data/replay_bars")
    ap.add_argument("--interval", action="append", default=None, help="tekrarlanabilir; varsayilan 1m ve 1h")
    ap.add_argument("--horizon", type=float, default=72.0, help="kayitli kapanistan sonra kac saat izlenecek")
    ap.add_argument("--adverse", action="store_true", help="ek olarak kotumser (aleyhte uc) mark modunu da kos")
    ap.add_argument("--be-mfe-from", default=None,
                    help="MFE basa-bas kuralinin URETIMDE devreye girdigi an (ISO). Verilirse ondan onceki "
                         "mumlarda kural KAPALI kosar — dagitim tarihine sadakat, ayar oynamasi degil.")
    args = ap.parse_args()
    be_from = from_iso(args.be_mfe_from) if args.be_mfe_from else None

    snap = Path(args.snapshot)
    ledger = json.loads((snap / "futures_ledger.json").read_text(encoding="utf-8"))
    targets: dict[str, list[float]] = {}
    ppath = snap / "position_path.jsonl"
    if ppath.exists():
        with io.open(ppath, encoding="utf-8") as fh:
            targets = path_targets_index(fh)

    plans = load_plans(ledger, targets)
    cfg = replay_config_from_ledger(ledger)
    src = BinanceBars(args.cache)
    intervals = args.interval or ["1m", "1h"]
    modes = ["close"] + (["adverse"] if args.adverse else [])

    rows: list[dict] = []
    for plan in plans:
        for interval in intervals:
            for mode in modes:
                out = replay_trade(plan, src, cfg, interval=interval, horizon_hours=args.horizon, mark_mode=mode,
                                   be_mfe_active_from=be_from)
                rows.append(compare(plan, out))
                print(f"{plan.trade_id} {plan.symbol:12} {interval:3} {mode:7} "
                      f"{rows[-1]['rec_exit']:>6} -> {rows[-1]['rep_exit']:<6} "
                      f"dR={rows[-1]['d_r'] if rows[-1]['d_r'] is None else round(rows[-1]['d_r'], 4)}", flush=True)

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    summaries = {f"{i}/{m}": summarise([r for r in rows if r["interval"] == i and r["mark_mode"] == m])
                 for i in intervals for m in modes}
    outp.write_text(json.dumps({"rows": rows, "summaries": summaries, "http": src.stats,
                              "be_mfe_from": args.be_mfe_from}, indent=1), encoding="utf-8")
    for key, s in summaries.items():
        print(f"\n== {key}: exit {s['exit_match']}/{s['n']}  |dR|<={s['r_tol']} {s['r_within']}/{s['n']}  "
              f"|dR_exfund| {s['r_exfund_within']}/{s['n']}  |dNet|<={s['net_tol']} {s['net_within']}/{s['n']}  "
              f"giris paritesi {s['entry_parity']}/{s['n']}")
    print(f"\nyazildi: {outp}  ({src.stats})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
