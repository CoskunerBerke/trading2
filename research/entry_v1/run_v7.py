# -*- coding: utf-8 -*-
"""PROTOCOL_V7 Q1: 3 rejim kolu x 3 pencere = 9 kosu. Temeller mevcut. Baska kosu YOK."""
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
WT = r"C:/Users/berke/wt-entry"
WINS = {"p1": ("2020-11-01", "2022-08-31"), "p2": ("2022-09-01", "2024-08-31"),
        "p3": ("2024-09-01", "2026-08-31")}
ARMS = [("r1", "r1_long_only_uptrend"), ("r2", "r2_no_trade_downtrend"), ("r3", "r3_follow_regime")]
JOBS = [("v7_%s_%s" % (a, p), rule, WINS[p]) for a, rule in ARMS for p in ("p1", "p2", "p3")]


def provenance():
    sha = subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", WT, "diff", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    untracked = subprocess.run(["git", "-C", WT, "ls-files", "--others", "--exclude-standard"],
                               capture_output=True, text=True).stdout
    for rel in untracked.split():
        p = Path(WT) / rel
        if p.suffix == ".py":
            diff += "\n# UNTRACKED %s\n" % rel + p.read_text(encoding="utf-8")
    (ROOT / "out" / "v7_code.patch").write_text(diff, encoding="utf-8")
    return {"wt_head": sha, "wt_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "regime_rules_sha256": hashlib.sha256((ROOT / "regime_rules.py").read_bytes()).hexdigest()}


def one(job):
    rid, rule, (f, t) = job
    cmd = [sys.executable, "run_rule.py", "--rule", rule, "--run-id", rid, "--from", f, "--to", t, "--trigger"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(el, 1), "rule": rule})
    if r.returncode != 0:
        m["err"] = r.stderr[-800:]
    print("%-10s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, m.get("n_opened")), flush=True)
    return m


if __name__ == "__main__":
    prov = provenance()
    print("PROTOCOL_V7 Q1: %d kosu, 9 paralel - %s" % (len(JOBS), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=9) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v7_meta.json").write_text(
        json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V7 DONE")
