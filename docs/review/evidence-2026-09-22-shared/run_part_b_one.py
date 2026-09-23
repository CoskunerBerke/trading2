# -*- coding: utf-8 -*-
"""Part B'nin TEK kolu (kural × yapı modu) — kolları paralel süreçlerde koşturmak için.

Kullanım: python run_part_b_one.py <kod_kökü (ölçülen SHA'nın çalışma ağacı)> <kural> <OFF|ENFORCE> <çıktı.json>
Kod `<kod_kökü>`ndeki `docs/review/evidence-2026-09-22-shared/real_archive_structures.py` üzerinden o ağacın koduyla yüklenir.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
name, mode, out = sys.argv[2], sys.argv[3], Path(sys.argv[4])
sys.path.insert(0, str(root / "docs" / "review" / "evidence-2026-09-22-shared"))
import real_archive_structures as R  # noqa: E402

assert Path(R.WT).resolve() == root, (R.WT, root)
sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
t = time.time()
syms = R._universe()
b = R.part_b(syms, names=(name,), modes=(mode,))
doc = {"label": "GERÇEK ARŞİV (bn_archive) — replay strateji modu, yapı %s; PnL raporlanmaz" % mode, "code_sha": sha,
       "symbols": syms, "window": ["2025-09-19", "2026-09-19"], "B_decision_effect": b, "elapsed_s": round(time.time() - t, 1)}
out.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print("yazıldı", out, doc["elapsed_s"])
