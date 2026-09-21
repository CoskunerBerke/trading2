# -*- coding: utf-8 -*-
"""Replay kosusunun olcutleri + blok bootstrap (PROTOCOL.md sec. 5)."""
import io, json, math, random, statistics as st
from pathlib import Path
ST = Path(r"C:/Users/berke/research/entry_v1/state/replay")

def load_full(run_id):
    """Ham kapanis kayitlari — `closed_at`, `bars`, `net` dahil (gunluk seri icin)."""
    d = json.load(io.open(ST/run_id/"futures_ledger.json", encoding="utf-8"))
    out = []
    for r in d["history"]:
        f = lambda k: float(r[k]) if r.get(k) not in (None, "") else 0.0
        out.append({"id": r["id"], "symbol": r["symbol"], "side": r["side"],
                    "opened_at": r["opened_at"], "closed_at": r["closed_at"],
                    "exit": r["exit_reason"], "net": f("net_pnl"), "r": f("r_multiple"),
                    "fees": f("fees"), "funding": f("funding"), "slip": f("slippage_cost"),
                    "bars": int(r.get("bars_held") or 0),
                    "regime": (r.get("features") or {}).get("regime"),
                    "month": r["closed_at"][:7]})
    return out, d


def load(run_id):
    d = json.load(io.open(ST/run_id/"futures_ledger.json", encoding="utf-8"))
    out = []
    for r in d["history"]:
        f = lambda k: float(r[k]) if r.get(k) not in (None, "") else None
        out.append({"id": r["id"], "symbol": r["symbol"], "side": r["side"],
                    "opened_at": r["opened_at"], "closed_at": r["closed_at"],
                    "exit": r["exit_reason"], "net": f("net_pnl"), "r": f("r_multiple"),
                    "fees": f("fees"), "funding": f("funding"), "bars": r.get("bars_held"),
                    "regime": (r.get("features") or {}).get("regime"),
                    "month": r["closed_at"][:7]})
    return out, d

def metrics(tr, led=None):
    if not tr:
        return {"n": 0}
    R = [t["r"] for t in tr]; N = [t["net"] for t in tr]
    w = [x for x in N if x > 0]; l = [-x for x in N if x <= 0]
    eq, peak, dd = 100.0, 100.0, 0.0
    for t in sorted(tr, key=lambda x: x["closed_at"]):
        eq += t["net"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    return {"n": len(tr), "net_usdt": round(sum(N), 4),
            "mean_r": round(st.mean(R), 4), "median_r": round(st.median(R), 4),
            "win_rate": round(sum(1 for x in R if x > 0)/len(R), 4),
            "profit_factor": round(sum(w)/sum(l), 4) if l else None,
            "max_drawdown_usdt": round(dd, 4),
            "mean_bars_held": round(st.mean([t["bars"] or 0 for t in tr]), 2),
            "fees_total": round(sum(t["fees"] or 0 for t in tr), 4),
            "funding_total": round(sum(t["funding"] or 0 for t in tr), 4),
            "symbols": len({t["symbol"] for t in tr}), "months": len({t["month"] for t in tr}),
            "final_equity": round(100.0 + sum(N), 4)}

def blocks(tr):
    """Blok = (sembol, takvim ayi). Ustuste binen islemler bagimsiz SAYILMAZ."""
    b = {}
    for t in tr:
        b.setdefault((t["symbol"], t["month"]), []).append(t)
    return list(b.values())

def boot_mean_r(tr, reps=2000, seed=7):
    bs = blocks(tr)
    if not bs: return (None, None, None)
    rnd = random.Random(seed); means = []
    for _ in range(reps):
        samp = [t for _ in bs for t in rnd.choice(bs)]
        if samp: means.append(st.mean(t["r"] for t in samp))
    means.sort()
    return (round(st.mean(means), 4), round(means[int(.025*len(means))], 4), round(means[int(.975*len(means))-1], 4))

def observed_diff(a, b):
    """GOZLENEN fark: iki kolun ornek ortalamalarinin farki. Bootstrap ORTALAMASI DEGIL.

    `boot_diff` blok yeniden ornekleme ortalamasini dondurur; bu, bloklari eslit olasilikla
    yeniden agirliklandirdigi icin gozlenen farktan SAPAR (olculdu: +0.0087 vs +0.0074).
    Nokta tahmini olarak DAIMA bu fonksiyon kullanilir; bootstrap yalniz ARALIK icindir.
    """
    if not a or not b:
        return None
    return round(st.mean(t["r"] for t in a) - st.mean(t["r"] for t in b), 4)


def boot_diff(a, b, reps=2000, seed=11, paired=True):
    """Fark aralignin blok bootstrap'i.

    `paired=True`: iki kol AYNI blok listesinde degerlendirilir (daha dar aralik).
    `paired=False`: her kol kendi blok evreninden cekilir (daha genis, muhafazakar).
    Doner: (bootstrap ortalamasi, alt sinir, ust sinir). NOKTA TAHMINI icin `observed_diff`.
    """
    ba, bb = {}, {}
    for t in a: ba.setdefault((t["symbol"], t["month"]), []).append(t)
    for t in b: bb.setdefault((t["symbol"], t["month"]), []).append(t)
    rnd = random.Random(seed); diffs = []
    if paired:
        keys = sorted(set(ba) | set(bb))
        for _ in range(reps):
            ks = [rnd.choice(keys) for _ in keys]
            ra = [t["r"] for k in ks for t in ba.get(k, [])]
            rb = [t["r"] for k in ks for t in bb.get(k, [])]
            if ra and rb: diffs.append(st.mean(ra) - st.mean(rb))
    else:
        ka, kb = sorted(ba), sorted(bb)
        for _ in range(reps):
            ra = [t["r"] for k in [rnd.choice(ka) for _ in ka] for t in ba[k]]
            rb = [t["r"] for k in [rnd.choice(kb) for _ in kb] for t in bb[k]]
            if ra and rb: diffs.append(st.mean(ra) - st.mean(rb))
    if not diffs: return (None, None, None)
    diffs.sort()
    return (round(st.mean(diffs), 4), round(diffs[int(.025*len(diffs))], 4), round(diffs[int(.975*len(diffs))-1], 4))

def drop_best_symbol(tr):
    by = {}
    for t in tr: by.setdefault(t["symbol"], 0.0); by[t["symbol"]] += t["net"]
    if not by: return None, None
    best = max(by, key=by.get)
    rest = [t for t in tr if t["symbol"] != best]
    return best, (round(st.mean(t["r"] for t in rest), 4) if rest else None)

def report(run_id):
    tr, led = load(run_id)
    m = metrics(tr, led)
    m["bootstrap_mean_r_95"] = boot_mean_r(tr)
    b, r_ = drop_best_symbol(tr)
    m["best_symbol"] = b; m["mean_r_without_best_symbol"] = r_
    m["run_id"] = run_id
    return m, tr

if __name__ == "__main__":
    import sys
    for rid in sys.argv[1:]:
        m, _ = report(rid)
        print(json.dumps(m, ensure_ascii=False))
