# -*- coding: utf-8 -*-
"""22 kosu: 4 referans + 18 yapilandirma. GERCEK borsa filtreleriyle (yeniden kosum)."""
from __future__ import annotations
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(r"C:/Users/berke/research/entry_v1")
sys.path.insert(0, str(ROOT))
import entry_rules  # noqa: E402
DEV = ("2022-09-01", "2026-09-01")
TEST = ("2024-09-01", "2026-09-01")
EVAL = ("2020-11-01", "2022-08-31")
PAR = 8

JOBS = [("base_gated", "none", TEST, True),
        ("base_ungated", "none", TEST, False),
        ("dev_base_ungated", "none", DEV, False),
        ("eval_base_ungated", "none", EVAL, False)]
JOBS += [("dev_" + n.replace(".", "_"), n, DEV, False) for n in entry_rules.all_names()]

def one(job):
    rid, rule, (f, t), gate = job
    cmd = [sys.executable, "run_rule.py", "--rule", rule, "--run-id", rid, "--from", f, "--to", t]
    if gate:
        cmd.append("--gate")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid, "rc": r.returncode, "stderr_tail": r.stderr[-600:]}
    meta["wall_s"] = round(el, 1)
    print("%-24s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, meta.get("n_opened")), flush=True)
    return meta

if __name__ == "__main__":
    print("kosu: %d, paralel: %d" % (len(JOBS), PAR), flush=True)
    with ThreadPoolExecutor(max_workers=PAR) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "run_all_meta.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("RUN ALL DONE")
