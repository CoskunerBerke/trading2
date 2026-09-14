# -*- coding: utf-8 -*-
"""PROTOCOL_V5: 4 kol x 2 pencere = 8 kosu (temel B0 = v4_c3, yeniden kosulmaz). Baska kosu YOK."""
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
WT = r"C:/Users/berke/wt-entry"
P1 = ("2020-11-01", "2022-08-31")
P2 = ("2022-09-01", "2024-08-31")
ARMS = [("p1", "p1_4h_confirm", "taze 4h ayni tarafli kirilis sart"),
        ("p2", "p2_4h_veto", "taze 4h karsi kirilis vetosu"),
        ("p3", "p3_1d_confirm", "taze 1d ayni tarafli kirilis sart"),
        ("p4", "p4_1d_veto", "taze 1d karsi kirilis vetosu")]
JOBS = [("v5_%s_%s" % (a, p), rule, win) for a, rule, _ in ARMS
        for p, win in (("p1", P1), ("p2", P2))]


def provenance():
    sha = subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", WT, "diff", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    untracked = subprocess.run(["git", "-C", WT, "ls-files", "--others", "--exclude-standard"],
                               capture_output=True, text=True).stdout
    for rel in untracked.split():
        p = Path(WT) / rel
        if p.suffix == ".py":
            diff += "\n# UNTRACKED %s\n" % rel + p.read_text(encoding="utf-8")
    (ROOT / "out" / "v5_code.patch").write_text(diff, encoding="utf-8")
    rules = (ROOT / "chart_rules.py").read_bytes()
    return {"wt_head": sha,
            "wt_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "chart_rules_sha256": hashlib.sha256(rules).hexdigest()}


def one(job):
    rid, rule, (f, t) = job
    cmd = [sys.executable, "run_rule.py", "--rule", rule, "--run-id", rid,
           "--from", f, "--to", t, "--trigger"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(el, 1), "rule": rule})
    if r.returncode != 0:
        m["err"] = r.stderr[-800:]
    print("%-10s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, m.get("n_opened")),
          flush=True)
    return m


if __name__ == "__main__":
    prov = provenance()
    print("PROTOCOL_V5: %d kosu, 8 paralel - %s" % (len(JOBS), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v5_meta.json").write_text(
        json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")
    print("V5 DONE")
