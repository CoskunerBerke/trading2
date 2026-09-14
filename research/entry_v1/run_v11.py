# -*- coding: utf-8 -*-
"""PROTOCOL_V11: 3 momentum kolu x 3 pencere = 9 kosu (strateji modu). Baska kosu YOK."""
import hashlib, json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(r"C:/Users/berke/research/entry_v1")
WINS = {"p1": ("2020-11-01", "2022-08-31"), "p2": ("2022-09-01", "2024-08-31"), "p3": ("2024-09-01", "2026-08-31")}
ARMS = ["m1_ema100", "m2_tsmom28", "m3_tsmom91"]
JOBS = [("v11_%s_%s" % (a.split("_")[0], p), a, WINS[p]) for a in ARMS for p in ("p1", "p2", "p3")]

def one(job):
    rid, strat, (f, t) = job
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--strategy", strat, "--run-id", rid, "--from", f, "--to", t, "--no-breakeven"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(time.time() - t0, 1), "strategy_name": strat})
    if r.returncode != 0:
        m["err"] = r.stderr[-1200:]
    print("%-10s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, m["wall_s"], m.get("n_opened")), flush=True)
    return m

if __name__ == "__main__":
    prov = {"momentum_rules_sha256": hashlib.sha256((ROOT / "momentum_rules.py").read_bytes()).hexdigest()}
    print("PROTOCOL_V11: %d kosu - %s" % (len(JOBS), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=9) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v11_meta.json").write_text(json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V11 DONE")
