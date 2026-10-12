"""P3b — parite (a) ve çekirdek hızı, YALNIZ sentetik veriyle (docs/ENGINE_BRAIN_V1.md §2.1, §5.3, §8.2).

Her K ailesi (kopyanın canlı defteri: T2, M2, BOX, D4, C4 = CV001…CV008 birlikte, FM) için belirlenimci sentetik dünyalar
(`research_engine/synthetic.py`, tohum 11, 12, …; 4h/1d dünyalarında tutarlı 1h alt barlarla) üretir; her dünyada
çekirdeği (`kernel.run`, `kernel_v2`) ve başvuruyu (canlı kural → `apply_action` → `FuturesLedgerV2.tick`,
`parity.reference_run`) İKİ kipte koşar: `live_1h` (KAPILI ölçü; canlı defterin tikleri — açılış fiyat tiki + sinyal
barının 1h OHLC barları, kırpma yok) ve `ohlc` (rapor; sinyal barı başına tek OHLC tiki); eşleşen işlem sayısı
(`live_1h`) `--min-trades`'e (varsayılan mühürlü `parity_kernel.min_synthetic_trades_per_family` = 200) ulaşana kadar
devam eder. Çıktı JSON: aile başına sinyal kimliği, medyan/p95/en büyük |ΔR|, işaretli ortalama (çekirdek − defter),
geçti mi (`live_1h`); `ohlc` kipinin ΔR'si; çekirdeğin ve defterin çıkış dayanakları (`exit_decision` `basis`);
`PATH_1H_GAP` ve `FUNDING_EXIT_BAR` işaretleri; bar/sn ve karar/sn hızı. Ağ yok, gerçek veri yok, hiçbir yere yazmaz
(`--out` verilmedikçe).

    python scripts/engine_p3b_measure.py [--families T2,M2,BOX,D4,C4,FM] [--min-trades 200] [--max-worlds 40] [--out f.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.research_engine import kernel as KN  # noqa: E402
from tradingbot.research_engine import parity as PA  # noqa: E402
from tradingbot.research_engine import registry as RG  # noqa: E402
from tradingbot.research_engine import synthetic as S  # noqa: E402
from tradingbot.strategy_lib import catalog as SLC  # noqa: E402


def family_variants(fam: str) -> list[str]:
    return [v for v in SLC.IMPLEMENTED if SLC.BOOK_OF[v] == fam]


def measure(fam: str, *, min_trades: int, max_worlds: int, seed0: int = 11, ohlc: bool = True) -> dict:
    pairs, pairs_ohlc = [], []
    bases: dict[str, int] = {}
    bars = decisions = 0
    t_kernel = t_ref = 0.0
    worlds = 0
    for k in range(max_worlds):
        data = S.WORLDS[fam](seed0 + k)
        worlds += 1
        for vid in family_variants(fam):
            var = KN.make_variant(vid)
            t = time.perf_counter()
            kr = KN.run(var, data)
            t_kernel += time.perf_counter() - t
            t = time.perf_counter()
            refs, _rej = PA.reference_run(vid, data, mode=PA.MODE_LIVE)
            t_ref += time.perf_counter() - t
            bars += kr.bars
            decisions += kr.decisions
            pairs.append((vid, kr, refs))
            for r in refs:
                b = str(r.exit_basis or r.exit_reason)
                bases[b] = bases.get(b, 0) + 1
            if ohlc:
                ro, _rj = PA.reference_run(vid, data, mode=PA.MODE_OHLC)
                pairs_ohlc.append((vid, kr, ro))
        a = PA.compare_a(pairs, min_trades=min_trades)
        print(f"[{fam}] dünya {worlds}: eşleşen {a.n_matched} / çekirdek {a.n_kernel} / defter {a.n_ref}", file=sys.stderr,
              flush=True)
        if a.n_matched >= min_trades:
            break
    a = PA.compare_a(pairs, min_trades=min_trades)
    trades = [t for _v, kr, _r in pairs for t in kr.trades]
    mean = {sc: (sum(t.r[sc] for t in trades) / len(trades) if trades else None) for sc in KN.SCENARIOS}
    amb = [t.funding_ambiguous_usdt / t.risk_usdt for t in trades if KN.FLAG_FUNDING_EXIT_BAR in t.flags and t.risk_usdt]
    kbases: dict[str, int] = {}
    for t in trades:
        b = str(t.legs[-1].basis or t.exit_reason) if t.legs else str(t.exit_reason)
        kbases[b] = kbases.get(b, 0) + 1
    out_ohlc = None
    if ohlc:
        b = PA.compare_a(pairs_ohlc, min_trades=min_trades)
        out_ohlc = dict(b.to_dict(), passed=None)
    return {"family": fam, "variants": family_variants(fam), "worlds": worlds, "bars": bars, "decisions": decisions,
            "parity_a": dict(a.to_dict(), mode=PA.MODE_LIVE, ledger_exit_basis=dict(sorted(bases.items()))),
            "parity_a_ohlc_report": out_ohlc, "kernel_exit_basis": dict(sorted(kbases.items())),
            "path_1h_gap_trades": sum(1 for t in trades if KN.FLAG_PATH_GAP in t.flags),
            "funding_exit_bar_trades": len(amb),
            "funding_exit_bar_mean_abs_r": (sum(abs(x) for x in amb) / len(amb)) if amb else 0.0,
            "kernel_seconds": round(t_kernel, 3), "reference_seconds": round(t_ref, 3),
            "kernel_bars_per_sec": round(bars / t_kernel, 1) if t_kernel else None,
            "kernel_decisions_per_sec": round(decisions / t_kernel, 1) if t_kernel else None,
            "trades_per_1000_bars": round(1000.0 * len(trades) / bars, 3) if bars else None,
            "mean_r_by_scenario_synthetic": mean,
            "exit_kinds": {k: sum(1 for t in trades if t.exit_kind == k) for k in sorted({t.exit_kind for t in trades})},
            "funding_gap_trades": sum(1 for t in trades if KN.FLAG_FUNDING_GAP in t.flags)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--families", default="T2,M2,BOX,D4,C4,FM")
    ap.add_argument("--min-trades", type=int,
                    default=int(RG.LIB_V1["thresholds"]["parity_kernel"]["min_synthetic_trades_per_family"]))
    ap.add_argument("--max-worlds", type=int, default=40)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-ohlc", action="store_true", help="sinyal barı başına tek OHLC tiki rapor kipini koşma")
    args = ap.parse_args(argv)
    out = {"kernel": KN.KERNEL_VERSION, "thresholds": RG.thaw(RG.LIB_V1["thresholds"]["parity_kernel"]),
           "registry_sha": RG.REGISTRY_SHA, "families": []}
    for fam in [f.strip() for f in args.families.split(",") if f.strip()]:
        out["families"].append(measure(fam, min_trades=args.min_trades, max_worlds=args.max_worlds, ohlc=not args.no_ohlc))
        print(json.dumps(out["families"][-1], ensure_ascii=False, sort_keys=True), flush=True)
    out["all_passed"] = all(f["parity_a"]["passed"] for f in out["families"])
    text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
