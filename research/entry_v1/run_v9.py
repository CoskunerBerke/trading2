# -*- coding: utf-8 -*-
"""PROTOCOL_V9: 3 strateji kolu x 3 pencere = 9 kosu (uzman yigini atlanir, hizli)."""
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
WT = r"C:/Users/berke/wt-entry"
WINS = {"p1": ("2020-11-01", "2022-08-31"), "p2": ("2022-09-01", "2024-08-31"), "p3": ("2024-09-01", "2026-08-31")}
ARMS = [("t1", "t1_trend", ["--no-breakeven"]), ("t2", "t2_trend_regime", ["--no-breakeven"]), ("t3", "t3_trend_botexit", [])]
JOBS = [("v9_%s_%s" % (a, p), strat, fl, WINS[p]) for a, strat, fl in ARMS for p in ("p1", "p2", "p3")]


def provenance():
    sha = subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", WT, "diff", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    (ROOT / "out" / "v9_code.patch").write_text(diff, encoding="utf-8")
    return {"wt_head": sha, "wt_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "strategy_rules_sha256": hashlib.sha256((ROOT / "strategy_rules.py").read_bytes()).hexdigest()}


def one(job):
    rid, strat, flags, (f, t) = job
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--strategy", strat, "--run-id", rid, "--from", f, "--to", t] + flags
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(el, 1), "strategy_name": strat, "flags": flags})
    if r.returncode != 0:
        m["err"] = r.stderr[-1200:]
    print("%-10s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, m.get("n_opened")), flush=True)
    return m


if __name__ == "__main__":
    prov = provenance()
    print("PROTOCOL_V9: %d kosu, 9 paralel - %s" % (len(JOBS), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=9) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v9_meta.json").write_text(json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V9 DONE")
