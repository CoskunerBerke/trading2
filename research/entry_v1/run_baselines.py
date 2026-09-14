# -*- coding: utf-8 -*-
"""Uretim-sadik (kapili) ve kapisiz taban kosulari — ayni pencere, ayni veri."""
import json
from run_replay import run
F, T = "2024-09-01", "2026-09-01"
rows = []
for rid, gate in (("base_gated", True), ("base_ungated", False)):
    m = run(rid, None, None, F, T, gate=gate)
    rows.append(m)
    print("%-14s gate=%-5s rc=%s %.0fs kararlar=%s acilan=%s" % (rid, gate, m["rc"], m["elapsed_s"], m.get("n_decisions"), m.get("n_opened")), flush=True)
json.dump(rows, open("out/baselines_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("BASELINES DONE")
