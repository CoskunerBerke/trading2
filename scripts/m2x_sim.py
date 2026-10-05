# -*- coding: utf-8 -*-
"""M2X SİMÜLASYONU (m2x_v1) — komut satırı. Ön kayıt: docs/M2_AGGRESSIVE_V1.md §3; politika ve mühür
tradingbot/m2x_policy.py (`M2X_POLICY_V1`, `M2X_POLICY_SHA`); simülasyon tradingbot/m2x_sim.py. PAPER/geçmiş testtir;
canlı defterlere, state/ klasörüne ve ayarlara DOKUNMAZ (sandbox: <out>/run_*/state).

    python scripts/m2x_sim.py --stage fetch                      # data.binance.vision arşivi → önbellek
    python scripts/m2x_sim.py --stage cfcheck,m2                 # karşı-olgusal denetimi + kol (a) (gerçek StrategyBook)
    python scripts/m2x_sim.py --stage arms --jobs 3              # M2X kolları (b, p, c, d, e, f, g, h1, h2, i1, i2, j)
    python scripts/m2x_sim.py --stage boot --jobs 3              # politika-tekrar bootstrap'ı (2.000 yol, tohum 20261005)
    python scripts/m2x_sim.py --stage report                     # <out>/report.json

Fiyat verisi depoya yüklenmez.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot import m2x_policy as P  # noqa: E402
from tradingbot import m2x_sim as S  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="M2X simülasyonu (ön kayıtlı; geçmiş test, PAPER).")
    ap.add_argument("--stage", default="cfcheck,m2,arms,boot,report")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--cache", default=str(ROOT / "m2x_data"))
    ap.add_argument("--out", default=str(ROOT / "m2x_out"))
    ap.add_argument("--jobs", type=int, default=max(1, min(3, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--paths", type=int, default=S.BOOT_PATHS)
    a = ap.parse_args(argv)
    stages = [x.strip().lower() for x in a.stage.split(",") if x.strip()]
    print("m2x_sim %s · politika %s · mühür M2X_POLICY_SHA = %s" % (S.SIM_VERSION, P.POLICY_VERSION, P.M2X_POLICY_SHA),
          flush=True)
    S.run_study(config_path=Path(a.config), cache_dir=Path(a.cache), out_dir=Path(a.out), stages=stages, jobs=a.jobs,
                n_paths=a.paths, log=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
