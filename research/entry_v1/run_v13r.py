# -*- coding: utf-8 -*-
"""PROTOCOL_V13R: 60 LOO + 15 komsu/kontrol + 6 kaydirilmis pencere = 81 kosu. Baska kosu YOK."""
import hashlib, json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(r"C:/Users/berke/research/entry_v1")
WINS = {"p1": ("2020-11-01", "2022-08-31"), "p2": ("2022-09-01", "2024-08-31"), "p3": ("2024-09-01", "2026-08-31")}
SHIFT = {"p1": ("2021-02-01", "2022-11-30"), "p2": ("2022-12-01", "2024-11-30"), "p3": ("2024-12-01", "2026-08-31")}
COINS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]
BASE = {"t2": "t2_trend_regime", "m2": "m2_tsmom28"}
JOBS = []
for k, strat in BASE.items():
    for p, w in WINS.items():
        for c in COINS:
            JOBS.append(("v13r_loo_%s_%s_%s" % (k, p, c.split("/")[0]), strat, w, [c]))
for v in ("m2_tsmom21", "m2_tsmom42", "t2r_ema200", "t2r_ema150", "t2r_ema250"):
    for p, w in WINS.items():
        JOBS.append(("v13r_%s_%s" % (v, p), v, w, None))
for k, strat in BASE.items():
    for p, w in SHIFT.items():
        JOBS.append(("v13r_shift_%s_%s" % (k, p), strat, w, None))
assert len(JOBS) == 81, len(JOBS)


def one(job):
    rid, strat, (f, t), ex = job
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--strategy", strat, "--run-id", rid,
           "--from", f, "--to", t, "--no-breakeven"]
    if ex:
        cmd += ["--exclude"] + ex
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(time.time() - t0, 1), "strategy_name": strat})
    if r.returncode != 0:
        m["err"] = r.stderr[-1200:]
    print("%-28s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, m["wall_s"], m.get("n_opened")), flush=True)
    return m


if __name__ == "__main__":
    only = sys.argv[1:]            # istege bagli: yalniz bu run_id'ler (duman testi)
    jobs = [j for j in JOBS if not only or j[0] in only]
    prov = {"momentum_rules_sha256": hashlib.sha256((ROOT / "momentum_rules.py").read_bytes()).hexdigest(),
            "run_rule_sha256": hashlib.sha256((ROOT / "run_rule.py").read_bytes()).hexdigest()}
    print("PROTOCOL_V13R: %d kosu - %s" % (len(jobs), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=12) as ex:
        rows = list(ex.map(one, jobs))
    out = ROOT / "out" / ("v13r_meta%s.json" % ("_smoke" if only else ""))
    out.write_text(json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V13R DONE", sum(1 for r in rows if r.get("rc") == 0), "/", len(rows))
