"""Onceden kayit altina alinan kabul olcutlerini (A1..A7) UYGULAR ve karari yazar.

Bu betik olcutleri YENIDEN TANIMLAMAZ; `docs/CHALLENGERS_V1.md`'de yazili olani okur ve hesaplar.
Duen bir rakip dusmus olarak raporlanir.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.replay.challengers import C1, C2, C3_FIELD, C3_TAU  # noqa: E402
from tradingbot.replay.stats import MIN_CELL, cluster_bootstrap, holm, mean  # noqa: E402

MIN_EFFECT = 0.10                       # A1 — eyleme gecirilebilirlik tabani (R/islem)
CLU = lambda r: f"{r['day']}|{r['symbol']}"                                       # noqa: E731


def paired_delta(rows, pol_name, n_boot=5000, cluster=CLU):
    """C1/C2 — aday basina eslestirilmis fark. Gun etkisi tam olarak sadelesir."""
    pairs = [dict(r, d=r[pol_name]["r"] - r["baseline"]["r"]) for r in rows
             if r["baseline"]["r"] is not None and r[pol_name]["r"] is not None and not r[pol_name]["excluded"]]
    b = cluster_bootstrap(pairs, lambda r: r["d"], cluster, n_boot=n_boot)
    draws_p = _p_from(pairs, cluster, n_boot)
    b["p_two_sided"] = draws_p
    b["excluded"] = sum(1 for r in rows if r[pol_name].get("excluded"))
    return b, pairs


def _p_from(pairs, cluster, n_boot):
    import random
    g = {}
    for r in pairs:
        g.setdefault(cluster(r), []).append(r["d"])
    keys = list(g)
    rng = random.Random(20260909)
    below = 0
    for _ in range(n_boot):
        s = []
        for _ in range(len(keys)):
            s.extend(g[keys[rng.randrange(len(keys))]])
        if s and mean(s) <= 0:
            below += 1
    f = below / n_boot
    return min(1.0, 2 * min(f, 1 - f))


def subset_delta(rows, field, tau, n_boot=5000, cluster=CLU):
    """C3 — gun-ortalamasi cikarilmis R uzerinde (esigi gecen) − (gecemeyen), ORTAK kume cekilisiyle."""
    import collections
    import random
    ok = [r for r in rows if r["baseline"]["r"] is not None]
    bym = collections.defaultdict(list)
    for r in ok:
        bym[r["day"]].append(r["baseline"]["r"])
    dm = {k: mean(v) for k, v in bym.items()}
    ga, gb = {}, {}
    for r in ok:
        v = r["baseline"]["r"] - dm[r["day"]]
        (ga if r[field] >= tau else gb).setdefault(cluster(r), []).append(v)
    keys = sorted(set(ga) | set(gb))
    fa = [v for k in keys for v in ga.get(k, [])]
    fb = [v for k in keys for v in gb.get(k, [])]
    point = mean(fa) - mean(fb)
    rng = random.Random(20260909)
    draws = []
    for _ in range(n_boot):
        sa, sb = [], []
        for _ in range(len(keys)):
            k = keys[rng.randrange(len(keys))]
            sa.extend(ga.get(k, []))
            sb.extend(gb.get(k, []))
        if sa and sb:
            draws.append(mean(sa) - mean(sb))
    draws.sort()
    below = sum(1 for x in draws if x <= 0) / len(draws)
    return {"n": len(fa) + len(fb), "n_pass": len(fa), "n_fail": len(fb), "n_clusters": len(keys),
            "point": point, "lo": draws[int(0.025 * len(draws))], "hi": draws[int(0.975 * len(draws))],
            "p_two_sided": min(1.0, 2 * min(below, 1 - below)),
            "underpowered": len(keys) < MIN_CELL or min(len(fa), len(fb)) < MIN_CELL,
            "mean_pass": mean(fa), "mean_fail": mean(fb)}


def leave_one_day_out(rows, fn):
    """A4 — her UTC gunu sirayla dusur; isaret korunuyor mu?"""
    days = sorted({r["day"] for r in rows})
    out = {}
    for d in days:
        sub = [r for r in rows if r["day"] != d]
        out[d] = fn(sub)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True, help="challenger_eval ciktisi (birincil ufuk)")
    ap.add_argument("--eval-48", default=None, help="A5 icin 48h ciktisi")
    ap.add_argument("--fidelity", default=None, help="A6 icin gercek 29 islem sonucu")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    d = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r["mature"] and r["baseline"]["r"] is not None]
    res: dict = {"population": {"n": len(rows), "clusters": len({CLU(r) for r in rows}),
                                "symbols": len({r["symbol"] for r in rows}),
                                "days": sorted({r["day"] for r in rows}),
                                "baseline_mean_r": mean([r["baseline"]["r"] for r in rows])},
                 "min_effect_R": MIN_EFFECT, "challengers": {}}

    for pol in (C1, C2):
        b, pairs = paired_delta(rows, pol.name)
        b["loo_day"] = {k: paired_delta(v, pol.name, n_boot=800)[0]["point"]
                        for k, v in leave_one_day_out(rows, lambda s: s).items()}
        b["cluster_symbol"] = cluster_bootstrap(pairs, lambda r: r["d"], lambda r: r["symbol"], n_boot=3000)
        b["cluster_day"] = cluster_bootstrap(pairs, lambda r: r["d"], lambda r: r["day"], n_boot=3000)
        b["mean_r_challenger"] = mean([r[pol.name]["r"] for r in pairs])
        b["mean_r_baseline"] = mean([r["baseline"]["r"] for r in pairs])
        b["win_rate_challenger"] = sum(1 for r in pairs if r[pol.name]["r"] > 0) / len(pairs)
        b["win_rate_baseline"] = sum(1 for r in pairs if r["baseline"]["r"] > 0) / len(pairs)
        res["challengers"][pol.name] = b

    c3 = subset_delta(rows, C3_FIELD, C3_TAU)
    c3["loo_day"] = {k: subset_delta(v, C3_FIELD, C3_TAU, n_boot=800)["point"]
                     for k, v in leave_one_day_out(rows, lambda s: s).items()}
    res["challengers"]["C3_gate_median"] = c3

    if args.eval_48:
        d48 = json.loads(Path(args.eval_48).read_text(encoding="utf-8"))
        r48 = [r for r in d48["rows"] if r["mature"] and r["baseline"]["r"] is not None]
        res["horizon_48h"] = {
            C1.name: paired_delta(r48, C1.name, n_boot=2000)[0],
            C2.name: paired_delta(r48, C2.name, n_boot=2000)[0],
            "C3_gate_median": subset_delta(r48, C3_FIELD, C3_TAU, n_boot=2000),
        }
    if args.fidelity:
        res["realised_29"] = json.loads(Path(args.fidelity).read_text(encoding="utf-8"))

    pvals = {k: v["p_two_sided"] for k, v in res["challengers"].items()}
    res["holm"] = holm(pvals, alpha=0.05)

    for name, b in res["challengers"].items():
        a1 = b["point"] >= MIN_EFFECT
        a2 = b["lo"] > 0
        a3 = bool(res["holm"][name]["reject_at_fwer"]) and b["point"] > 0
        a4 = all(v > 0 for v in b["loo_day"].values()) if b["point"] > 0 else False
        a5 = None
        if args.eval_48:
            a5 = res["horizon_48h"][name]["point"] > 0 if b["point"] > 0 else res["horizon_48h"][name]["point"] < 0
        a6 = None
        if args.fidelity and name in res.get("realised_29", {}):
            # A6 ISARET TUTARLILIGI olcer, iyilik degil: havuzdaki isaret havuz disi 29 islemde de
            # tekrarliyor mu? Duen bir rakip icin de bilgi vericidir, o yuzden kosulsuz hesaplanir.
            a6 = (res["realised_29"][name]["mean_delta_r"] > 0) == (b["point"] > 0)
        a7 = None if name != "C3_gate_median" else "NOT_REACHED"
        crit = {"A1_effect_ge_0.10R": a1, "A2_CI_excludes_0": a2, "A3_holm_fwer": a3,
                "A4_leave_one_day_out": a4, "A5_horizon_48h": a5, "A6_realised_29": a6,
                "A7_portfolio": a7}
        b["criteria"] = crit
        hard = [a1, a2, a3, a4]
        b["verdict"] = "ACCEPTED" if all(hard) and (a5 is not False) and (a6 is not False) else "NOT ACCEPTED"
        if b.get("underpowered"):
            b["verdict"] = "UNDERPOWERED / NOT ACCEPTED"

    Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    for name, b in res["challengers"].items():
        print(f"\n{name}")
        print(f"  n={b['n']} clusters={b['n_clusters']} effect={b['point']:+.4f} R "
              f"95%CI[{b['lo']:+.4f},{b['hi']:+.4f}] p={b['p_two_sided']:.4f}")
        print(f"  criteria={b['criteria']}")
        print(f"  VERDICT: {b['verdict']}")
    print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
