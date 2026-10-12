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

P3b-2a: saf aileler (`B`, `C`, `D`, `E`, `F1`, `F2`, `H`) de ölçülür — her varyant kendi dünyasında
(`synthetic.world_for`: 1d → trend, 4h → D4, 1h → 1h dünyası, H → kalıcı fonlama rejimli 4h — 2026-10-12 inceleme
düzeltmesinden beri 8h → 4h geçişi, ms kaymaları ve iki eksik uzlaşmayla; F2 → Box 5m); başvuru varyantın KENDİ kuralıdır
(`parity._PureRule`; canlı karşılığı yok) → ölçü çekirdeğin yol/maliyet aritmetiğini sınar. `f8_unmeasurable_trades`:
açık pozisyonda f8 ölçülemediği için tutulan (bilgi işareti `F8_UNMEASURABLE`) işlem sayısı.
`--speed-only`: yalnız çekirdek hızı (başvuru koşulmaz). `--box-days`: F2 Box dünyasının günü (varsayılan 60).

P3b-2b: O örtüleri taban başına bir aile birimidir (`O_T2`, `O_M2`, `O_BOX`, `O_D4`, `O_C4`; ya da hepsi için `O`): her
dünyada tabanın ailesi TEK paylaşılan akışla kurulur (`kernel.make_family`), önce K satırının koşusu (C4'te K satırı
yoktur; taban C4 defteridir) akışı doldurur, sonra yedi örtü; parite (a) her örtü için başvuruya (örtünün kendi kuralı →
`apply_action` → `FuturesLedgerV2`) karşı. Box örtülerinin dünyası `--box-days-o` gün (varsayılan 150: F_ER/F_VOL 1d
barları ister). Aile `kernel.run_lockstep` ile bar bar koşulur (K satırı + yedi örtü; sonuçlar ayrı koşularla bayt-aynı).
Hız: `kernel_seconds` = ailenin bütün çekirdek koşuları (K satırı dahil); `family_pass_bars_per_sec` = seri barı / (K +
yedi örtü) süresi; `variant_bars_per_sec` = koşu başına etkin hız (seri barı × koşu sayısı / süre). `--slow`: K/F2 ailelerini düz yolla (`fast=False`: pandas çerçevesi, ön-süzgeç yok)
ölçer (P3b-2b öncesi hız, aynı makinede kıyas için).

    python scripts/engine_p3b_measure.py [--families T2,M2,BOX,D4,C4,FM] [--min-trades 200] [--max-worlds 40] [--out f.json]
    python scripts/engine_p3b_measure.py --families B,C,D,E,F1,F2,H [--speed-only]
    python scripts/engine_p3b_measure.py --families O_T2,O_M2,O_BOX,O_D4,O_C4 [--speed-only]
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


PURE_FAMILIES = ("B", "C", "D", "E", "F1", "F2", "H")
COVER_FAMILIES = ("O_T2", "O_M2", "O_BOX", "O_D4", "O_C4")


def family_variants(fam: str) -> list[str]:
    if fam in PURE_FAMILIES:
        return [v for v in SLC.IMPLEMENTED if SLC.FAMILY_OF[v] == fam]
    if fam in COVER_FAMILIES:
        return KN.covers_of(fam[2:])
    return [v for v in SLC.IMPLEMENTED if SLC.BOOK_OF.get(v) == fam]


_WORLDS: dict[tuple, object] = {}


def world_of(fam: str, vid: str, seed: int, box_days: int):
    """K: ailenin dünyası (P3b-1 ile aynı); saf aile: varyantın dilimine göre (`synthetic.world_for`)."""
    if fam not in PURE_FAMILIES:
        return S.WORLDS[fam](seed)
    var = KN.make_variant(vid)
    fn = S.world_for(vid, var.signal_tf, var.family)
    key = (fn.__name__, seed)
    if key not in _WORLDS:
        _WORLDS.clear() if len(_WORLDS) > 8 else None
        _WORLDS[key] = fn(seed, days=box_days) if var.family == "F2" else fn(seed)
    return _WORLDS[key]


def measure_covers(fam: str, *, min_trades: int, max_worlds: int, seed0: int = 11, speed_only: bool = False,
                   box_days: int = 150) -> dict:
    """O ailesi (taban başına): paylaşılan akışla taban + yedi örtü; parite (a) her örtü için (modül belgesi)."""
    base = fam[2:]
    vids = family_variants(fam)
    pairs: list = []
    per_variant: dict[str, list[float]] = {}
    bases: dict[str, int] = {}
    worlds = series_bars = 0
    t_kernel = t_ref = 0.0
    hits = {"signals_hit": 0, "signals_miss": 0, "manage_hit": 0, "manage_miss": 0, "manage_unshared": 0}
    for k in range(max_worlds):
        worlds += 1
        data = S.box_world(seed0 + k, days=box_days) if base == "BOX" else S.WORLDS[base](seed0 + k)
        shared, members = KN.make_family(base)
        runners = ([shared] if base != "C4" else []) + list(members.values())
        t = time.perf_counter()
        results = KN.run_lockstep(runners, data)                # K satırı (C4'te yok) + yedi örtü, bar bar
        t_kernel += time.perf_counter() - t
        if base != "C4":
            results = results[1:]
        for (vid, var), kr in zip(members.items(), results):
            pv = per_variant.setdefault(vid, [0.0, 0.0])
            pv[0] += kr.bars
            refs = []
            if not speed_only:
                t = time.perf_counter()
                refs, _rej = PA.reference_run(vid, data, mode=PA.MODE_LIVE)
                t_ref += time.perf_counter() - t
                for r in refs:
                    b = str(r.exit_basis or r.exit_reason)
                    bases[b] = bases.get(b, 0) + 1
            pairs.append((vid, kr, refs))
        series_bars += len(data.bars[shared.signal_tf])
        for key in hits:
            hits[key] += shared.stats[key]
        a = PA.compare_a(pairs, min_trades=min_trades)
        print(f"[{fam}] dünya {worlds}: eşleşen {a.n_matched} / çekirdek {a.n_kernel} / defter {a.n_ref}", file=sys.stderr,
              flush=True)
        if a.n_matched >= min_trades or (speed_only and a.n_kernel >= min_trades):
            break
    a = PA.compare_a(pairs, min_trades=min_trades)
    trades = [t for _v, kr, _r in pairs for t in kr.trades]
    by_vid = {vid: sum(1 for v, kr, _r in pairs if v == vid for _t in kr.trades) for vid in vids}
    kbases: dict[str, int] = {}
    for t in trades:
        b = str(t.legs[-1].basis or t.exit_reason) if t.legs else str(t.exit_reason)
        kbases[b] = kbases.get(b, 0) + 1
    total = t_kernel
    return {"family": fam, "variants": vids, "worlds": worlds, "series_bars": series_bars,
            "parity_a": dict(a.to_dict(), mode=PA.MODE_LIVE, ledger_exit_basis=dict(sorted(bases.items()))),
            "trades_by_variant": by_vid, "kernel_exit_basis": dict(sorted(kbases.items())),
            "exit_kinds": {k: sum(1 for t in trades if t.exit_kind == k) for k in sorted({t.exit_kind for t in trades})},
            "move_stop_ignored_trades": sum(1 for t in trades if KN.FLAG_MOVE_STOP_IGNORED in t.flags),
            "gate_excluded_trades": sum(1 for t in trades if KN.gate_excluded(t)),
            "path_1h_gap_trades": sum(1 for t in trades if KN.FLAG_PATH_GAP in t.flags),
            "kernel_seconds": round(total, 3), "reference_seconds": round(t_ref, 3), "stream_stats": hits,
            "speed_only": speed_only, "runs_per_series": len(vids) + (base != "C4"),
            "family_pass_bars_per_sec": round(series_bars / total, 1) if total else None,
            "variant_bars_per_sec": round(sum(b for b, _s in per_variant.values()) * (len(vids) + (base != "C4")) / len(vids)
                                          / total, 1) if total else None}


def measure(fam: str, *, min_trades: int, max_worlds: int, seed0: int = 11, ohlc: bool = True, speed_only: bool = False,
            box_days: int = 60, slow: bool = False) -> dict:
    pairs, pairs_ohlc = [], []
    bases: dict[str, int] = {}
    bars = decisions = 0
    t_kernel = t_ref = 0.0
    worlds = 0
    per_variant: dict[str, list[float]] = {}
    for k in range(max_worlds):
        worlds += 1
        for vid in family_variants(fam):
            data = world_of(fam, vid, seed0 + k, box_days)
            var = KN.make_variant(vid)
            if slow and hasattr(var, "fast"):
                var.fast = False                                # P3b-2b öncesi düz yol (pandas çerçevesi, süzgeç yok)
            t = time.perf_counter()
            kr = KN.run(var, data)
            dt = time.perf_counter() - t
            t_kernel += dt
            pv = per_variant.setdefault(vid, [0.0, 0.0])
            pv[0] += kr.bars
            pv[1] += dt
            if speed_only:
                bars += kr.bars
                decisions += kr.decisions
                pairs.append((vid, kr, []))
                continue
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
        if a.n_matched >= min_trades or (speed_only and a.n_kernel >= min_trades):
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
            "kernel_seconds": round(t_kernel, 3), "reference_seconds": round(t_ref, 3), "slow_path": slow,
            "kernel_bars_per_sec_by_variant": {v: round(b / s, 1) for v, (b, s) in per_variant.items() if s},
            "speed_only": speed_only,
            "kernel_bars_per_sec": round(bars / t_kernel, 1) if t_kernel else None,
            "kernel_decisions_per_sec": round(decisions / t_kernel, 1) if t_kernel else None,
            "trades_per_1000_bars": round(1000.0 * len(trades) / bars, 3) if bars else None,
            "mean_r_by_scenario_synthetic": mean,
            "exit_kinds": {k: sum(1 for t in trades if t.exit_kind == k) for k in sorted({t.exit_kind for t in trades})},
            "funding_gap_trades": sum(1 for t in trades if KN.FLAG_FUNDING_GAP in t.flags),
            "f8_unmeasurable_trades": sum(1 for t in trades if "F8_UNMEASURABLE" in t.flags)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--families", default="T2,M2,BOX,D4,C4,FM")
    ap.add_argument("--min-trades", type=int,
                    default=int(RG.LIB_V1["thresholds"]["parity_kernel"]["min_synthetic_trades_per_family"]))
    ap.add_argument("--max-worlds", type=int, default=40)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-ohlc", action="store_true", help="sinyal barı başına tek OHLC tiki rapor kipini koşma")
    ap.add_argument("--speed-only", action="store_true", help="yalnız çekirdek hızı (başvuru/parite yok)")
    ap.add_argument("--box-days", type=int, default=60, help="F2 Box dünyasının günü")
    ap.add_argument("--box-days-o", type=int, default=150, help="Box örtülerinin (O_BOX) dünyasının günü")
    ap.add_argument("--slow", action="store_true", help="K/F2'yi düz yolla ölç (fast=False; P3b-2b öncesi hız)")
    ap.add_argument("--seed0", type=int, default=11)
    args = ap.parse_args(argv)
    out = {"kernel": KN.KERNEL_VERSION, "thresholds": RG.thaw(RG.LIB_V1["thresholds"]["parity_kernel"]),
           "registry_sha": RG.REGISTRY_SHA, "families": []}
    fams = [f.strip() for f in args.families.split(",") if f.strip()]
    fams = [x for f in fams for x in (COVER_FAMILIES if f == "O" else (f,))]
    for fam in fams:
        if fam in COVER_FAMILIES:
            out["families"].append(measure_covers(fam, min_trades=args.min_trades, max_worlds=args.max_worlds,
                                                  seed0=args.seed0, speed_only=args.speed_only, box_days=args.box_days_o))
        else:
            out["families"].append(measure(fam, min_trades=args.min_trades, max_worlds=args.max_worlds, seed0=args.seed0,
                                           ohlc=not args.no_ohlc, speed_only=args.speed_only, box_days=args.box_days,
                                           slow=args.slow))
        print(json.dumps(out["families"][-1], ensure_ascii=False, sort_keys=True), flush=True)
    out["all_passed"] = all(f["parity_a"]["passed"] for f in out["families"] if not f["speed_only"])
    text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
