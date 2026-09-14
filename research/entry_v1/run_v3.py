# -*- coding: utf-8 -*-
"""PROTOCOL_V3: 3 kol x 2 pencere = 6 kosu. Baska kosu EKLENMEYECEK."""
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(r"C:/Users/berke/research/entry_v1")
P1 = ("2020-11-01", "2022-08-31")
P2 = ("2022-09-01", "2024-08-31")
ARMS = [("a0", [], "tetiksiz (bugunku backtest)"),
        ("a1", ["--trigger"], "tetikli (uretim davranisi)"),
        ("a2", ["--trigger", "--veto"], "tetikli + sabirli motor vetosu")]
JOBS = [("v3_%s_p1" % a, fl, P1) for a, fl, _ in ARMS] + [("v3_%s_p2" % a, fl, P2) for a, fl, _ in ARMS]

def one(job):
    rid, flags, (f, t) = job
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--run-id", rid, "--from", f, "--to", t] + flags
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid, "rc": r.returncode, "err": r.stderr[-500:]}
    m["wall_s"] = round(el, 1)
    print("%-12s rc=%s %5.0fs acilan=%s" % (rid, r.returncode, el, m.get("n_opened")), flush=True)
    return m

if __name__ == "__main__":
    print("PROTOCOL_V3: %d kosu, 6 paralel" % len(JOBS), flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v3_meta.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V3 DONE")
