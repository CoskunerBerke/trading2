# -*- coding: utf-8 -*-
"""CIKIS: MFE ESIGIYLE SILAHLANAN GEVSEK TRAIL (2026-09-20).

TEMEL: 40 coin, URETIM YOLU (candidate_order=None — canlidaki davranis), kaldirac 1,
bakiye 100, no_breakeven=True. Yani karsilastirma kolu `run40.json` icindeki "40coin".
TEK DEGISKEN: (trail_arm_r, trail_dist_r).

NEDEN BU, BASA-BAS YA DA KISMI KAR DEGIL — ikisi de OLCULDU ve DUSTU:
  * basa-bas: getiri 3-3 yazi tura, maks dusus 6/6'da KOTULESTI.
  * kismi kar (tp1): iyimser sinirda bile net R %40-53 duser.
Ikisi de kuyrugu kesiyordu; kar bu defterlerde en iyi 3 isleme yogun.

TRAIL KUYRUGU KESMEZ: ancak MFE `arm` esigini astiktan SONRA silahlanir. Olculdu:
A=3R/D=1R 620 islemin yalniz 67'sine dokunur, 553'u BIT-AYNI kalir.

KOLLAR: (3,1) (3,2) (2,1). Ucu de ayni pencerelerde, ayni arsivde.
Raporlanan: getiri, ortalama R, maks dusus, islem sayisi ve TRAIL_GIVEBACK ile kapanan
islem sayisi — "kac islem gercekten dokunuldu" sayiyla gorunur, varsayilmaz.
"""
from __future__ import annotations

import io
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
sys.path.insert(0, str(ROOT))

ARCHIVE = r"C:/Users/berke/research/bn_archive"
OUT = Path(__file__).resolve().parent / "trail.json"
WINDOWS = [("P3", "2024-09-01", "2026-08-31"),
           ("P2", "2022-09-01", "2024-08-31"),
           ("P1", "2020-11-01", "2022-08-31")]
ARMS = [(3.0, 1.0), (3.0, 2.0), (2.0, 1.0)]

results = []
if OUT.exists():
    try:
        results = [r for r in json.load(io.open(OUT, encoding="utf-8")) if isinstance(r, dict)]
        print("surduruluyor: %d kayit" % len(results), flush=True)
    except Exception:
        results = []
DONE = {r["run_id"] for r in results if r.get("ok")}


def _emit():
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=1, default=str))


def _funding(fc):
    if not isinstance(fc, dict):
        return "KAPSAM_YOK"
    q, a = int(fc.get("queries") or 0), int(fc.get("answered") or 0)
    return "SORULMADI" if q == 0 else ("TAM" if a == q else "EKSIK(%d/%d)" % (a, q))


def main():
    import analyze
    import momentum_rules
    import run_rule
    import strategy_rules

    for wname, dfrom, dto in WINDOWS:
        for rule in ("m2_tsmom28", "t2_trend_regime"):
            for arm, dist in ARMS:
                rid = "tl_%s_%s_a%dd%d" % (rule.split("_")[0], wname.lower(), int(arm), int(dist))
                if rid in DONE:
                    print("%-3s %-4s A%gD%g -> ATLANDI" % (wname, rule.split("_")[0], arm, dist), flush=True)
                    continue
                row = {"pencere": wname, "rule": rule, "arm_r": arm, "dist_r": dist, "run_id": rid}
                t0 = time.time()
                try:
                    try:
                        s = strategy_rules.build(rule, trail_arm_r=arm, trail_dist_r=dist)
                    except (KeyError, TypeError):
                        s = momentum_rules.build(rule)
                    meta = run_rule.run(rid, None, dfrom, dto, strategy=s, no_breakeven=True,
                                        symbols=None, cache_dir=ARCHIVE, require_all=True)
                    tr, led = analyze.load(rid)
                    m = analyze.metrics(tr, led)
                    sebep = Counter(str(t.get("exit") or "?") for t in tr)
                    trail_n = sum(n for k, n in sebep.items() if "TRAIL" in k.upper())
                    row.update({"ok": True, "funding": _funding(meta.get("funding_coverage")),
                                "n_opened": meta.get("n_opened"), "n_kapanan": m.get("n"),
                                "trail_ile_kapanan": trail_n,
                                "cikis_sebepleri": dict(sebep.most_common(5)),
                                "getiri_pct": round(float(m.get("final_equity", 100.0)) - 100.0, 2),
                                "ort_R": m.get("mean_r"), "isabet": m.get("win_rate"),
                                "maks_dd": m.get("max_drawdown_usdt"), "pf": m.get("profit_factor")})
                except Exception as exc:
                    row.update({"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc),
                                "iz": traceback.format_exc()[-700:]})
                row["elapsed_s"] = round(time.time() - t0, 1)
                results.append(row)
                _emit()
                print("%-3s %-4s A%gD%g -> %s" % (wname, rule.split("_")[0], arm, dist,
                      ("getiri=%+7.1f%% n=%-4s trail_cikis=%-4s ortR=%-7s dd=%-7s %s" % (
                          row.get("getiri_pct") or 0.0, row.get("n_kapanan"), row.get("trail_ile_kapanan"),
                          row.get("ort_R"), row.get("maks_dd"), row.get("funding")))
                      if row.get("ok") else "HATA " + str(row.get("hata"))[:110]), flush=True)


if __name__ == "__main__":
    main()
    print("=== BITTI ===")
