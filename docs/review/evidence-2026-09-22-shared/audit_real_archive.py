# -*- coding: utf-8 -*-
"""GERÇEK ARŞİV — ileri yürüyüş bütünlük denetimi (önce/sonra karşılaştırması için tek betik).

Kullanım:  python audit_real_archive.py <kod_kökü> <çıktı.json>
`<kod_kökü>`: denetlenecek sürümün çalışma ağacı (ör. d8a31a9 için geçici worktree; `structures/audit.py` aynı dosya).
Veri GERÇEK (yerel `bn_archive`, Binance USDⓈ-M perpetual), salt okuma. 40 coin (config.yaml entry_universe),
1d son 365 bar + 4h son 720 bar, pencere 400 bar. Kârlılık/PnL YOK.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))
ARCH = Path(r"C:\Users\berke\research\bn_archive\history")
TF_MS = {"1d": 86_400_000, "4h": 14_400_000}


def main() -> int:
    import yaml

    from tradingbot.history import HistoryStore
    from tradingbot.structures.audit import walk_forward_audit
    from tradingbot.structures.catalog import POLICY_VERSION
    t0 = time.time()
    syms = list((yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8")) or {})["entry_universe"]["symbols"])
    hs = HistoryStore(ARCH)
    doc = {"label": "GERÇEK ARŞİV (bn_archive, Binance USDⓈ-M perpetual) — salt okuma; PnL yok", "code_root": str(ROOT),
           "policy_version": POLICY_VERSION, "symbols": syms, "by_tf": {}}
    for tf, n_eval in (("1d", 365), ("4h", 720)):
        agg = {"analyses": 0, "violations": Counter(), "info": Counter(), "examples": [], "distinct_records": 0,
               "confirmations_by_name": Counter(), "rejects": Counter(), "symbols_without_rows": [], "per_symbol_violations": {}}
        for sym in syms:
            df = hs.read("futures", sym, tf)
            rows = [{"timestamp": int(r.timestamp), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                     "close": float(r.close), "volume": float(r.volume)} for r in df.itertuples()]
            if len(rows) < 30:
                agg["symbols_without_rows"].append(sym)
                continue
            r = walk_forward_audit(rows, market="USDM_PERP", symbol=sym, timeframe=tf, step_ms=TF_MS[tf], n_eval=n_eval)
            agg["analyses"] += r["analyses"]
            agg["distinct_records"] += r["distinct_records"]
            for k in ("violations", "info", "confirmations_by_name", "rejects"):
                agg[k].update(r[k])
            if r["violations"]:
                agg["per_symbol_violations"][sym] = r["violations"]
            agg["examples"] += r["examples"][: max(0, 12 - len(agg["examples"]))]
            print(tf, sym, r["analyses"], r["violations"] or "0 ihlal", flush=True)
        doc["by_tf"][tf] = {k: (dict(v.most_common()) if isinstance(v, Counter) else v) for k, v in agg.items()}
    doc["elapsed_s"] = round(time.time() - t0, 1)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("yazıldı", OUT, doc["elapsed_s"], "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
