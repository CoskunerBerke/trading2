# -*- coding: utf-8 -*-
"""PROTOCOL_V4: 5 kol x 2 pencere = 10 kosu. Baska kosu EKLENMEYECEK."""
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
ARMS = [("c0", "none", "temel: tetikli, kuralsiz (= V3 A1)"),
        ("c1", "c1_4h", "son 4h barda ayni tarafli sekil"),
        ("c2", "c2_4h_confirm", "4h sekil + teyit bari (makale kurali)"),
        ("c3", "c3_4h_veto", "yalniz karsi-sekil vetosu"),
        ("c4", "c4_1d", "son 1d barda ayni tarafli sekil")]
JOBS = [("v4_%s_%s" % (a, p), rule, win) for a, rule, _ in ARMS
        for p, win in (("p1", P1), ("p2", P2))]


def provenance():
    sha = subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", WT, "diff", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    (ROOT / "out" / "v4_code.patch").write_text(diff, encoding="utf-8")
    rules = (ROOT / "candle_rules.py").read_bytes()
    return {"wt_head": sha,
            "wt_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "candle_rules_sha256": hashlib.sha256(rules).hexdigest()}


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
    print("PROTOCOL_V4: %d kosu, 10 paralel - %s" % (len(JOBS), json.dumps(prov)), flush=True)
    with ThreadPoolExecutor(max_workers=10) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v4_meta.json").write_text(
        json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")
    print("V4 DONE")
