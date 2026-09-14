# -*- coding: utf-8 -*-
"""GERCEK uretim sinyali (18 uzman, analog dahil) — kapili ve kapisiz."""
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(r"C:/Users/berke/research/entry_v1")
W = ("2024-09-01", "2026-09-01")
JOBS = [("real_gated", True), ("real_ungated", False)]

def one(job):
    rid, gate = job
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--run-id", rid, "--from", W[0], "--to", W[1]]
    if gate:
        cmd.append("--gate")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid, "rc": r.returncode, "err": r.stderr[-600:]}
    m["wall_s"] = round(el, 1)
    print("%-14s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, m.get("n_opened")), flush=True)
    return m

if __name__ == "__main__":
    print("GERCEK SINYAL kosulari (18 uzman): %s -> %s" % W, flush=True)
    with ThreadPoolExecutor(max_workers=2) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "real_meta.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("REAL DONE")
