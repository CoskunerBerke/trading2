# -*- coding: utf-8 -*-
"""18 yapilandirmanin GELISTIRME penceresi kosusu — PROTOCOL_V2.md sec. 2-3.

Paralellik yalniz duvar saati icindir; her kosu ayri surec, ayri replay dizini, ayri
determinizm hash'i. Bilimsel icerige etkisi YOKTUR.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
DEV_FROM, DEV_TO = "2022-09-01", "2026-09-01"
PAR = 6

sys.path.insert(0, str(ROOT))
import entry_rules  # noqa: E402


def one(name: str) -> dict:
    rid = "dev_" + name.replace(".", "_")
    t0 = time.time()
    r = subprocess.run([sys.executable, "run_rule.py", "--rule", name, "--run-id", rid,
                        "--from", DEV_FROM, "--to", DEV_TO],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    el = time.time() - t0
    meta = {"rule": name, "run_id": rid, "rc": r.returncode, "wall_s": round(el, 1)}
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    if mp.exists():
        meta.update(json.loads(mp.read_text(encoding="utf-8")))
    else:
        meta["stderr_tail"] = r.stderr[-800:]
    print("%-16s rc=%s %5.0fs acilan=%s" % (name, meta["rc"], el, meta.get("n_opened")), flush=True)
    return meta


if __name__ == "__main__":
    names = entry_rules.all_names()
    print("yapilandirma: %d, paralel: %d, pencere: %s -> %s" % (len(names), PAR, DEV_FROM, DEV_TO), flush=True)
    with ThreadPoolExecutor(max_workers=PAR) as ex:
        rows = list(ex.map(one, names))
    (ROOT / "out" / "grid_rules_meta.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("GRID RULES DONE")
