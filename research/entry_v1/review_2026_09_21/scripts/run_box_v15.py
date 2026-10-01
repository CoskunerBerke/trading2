# -*- coding: utf-8 -*-
"""BOX THEORY V15 — süpürme koşusu.

AŞAMA A (belirleyici soru): kuralın stopu bir 5m mumu kadar dar; ölçülen medyan stop fiyatın %0,25'i ve
gidiş-dönüş maliyet (%0,16) riskin ~%80'ini yiyor. Bu yüzden ilk süpürme iki eksenlidir:

  * `min_stop_pct` — stop maliyetin yanında anlamlı olacak kadar geniş mi (BİZİM eşiğimiz, videoda yok).
  * `exit_kind` — çıkış videoda HİÇ söylenmiyor; kutu karşı kenarı / kutu ortası / R katları / yok.

AŞAMA B: A bir şey gösterirse tetik/long-stop/bant/taraf/stride kolları.

Karar kuralı (önceden yazılır, sonuca bakılarak DEĞİŞTİRİLMEZ): bir kolun "işe yarıyor" sayılması için
ÜÇ pencerede de NET ortalama R'nin %95 güven aralığı sıfırın ÜSTÜNDE kalmalı ve her pencerede en az
30 işlem olmalı. Tek pencerede pozitif olmak yeterli değildir (bkz. bölme deney değildir).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import box_sweep as B  # noqa: E402
from tradingbot.box_theory import BoxParams  # noqa: E402

BASE = BoxParams(leverage=3)

#: Giriş aileleri: (parametre, stride). Her biri BİR tarama.
#: Eşik değerleri ÜRETİM UYGUNLUĞUNDAN seçildi, yuvarlak sayı olsun diye değil: işlem ancak
#: `stop% >= risk% / (max_position_pct/100 * kaldıraç)` iken boyutlandırılabiliyor —
#: kaldıraç 3'te %2,22 · kaldıraç 5'te %1,33. Izgara bu iki eşiği ve altını kapsar.
STAGE_A_ENTRIES = [(BoxParams(leverage=3, min_stop_pct=m), 1)
                   for m in (0.0, 0.3, 0.6, 1.0, 1.33, 2.22)]

#: Çıkış kolları — aynı tarama üzerinde ucuz koşar.
STAGE_A_EXITS = [BoxParams(exit_kind="box_opposite"), BoxParams(exit_kind="box_mid"),
                 BoxParams(exit_kind="r_multiple", exit_r=1.0),
                 BoxParams(exit_kind="r_multiple", exit_r=1.5),
                 BoxParams(exit_kind="r_multiple", exit_r=2.0),
                 BoxParams(exit_kind="r_multiple", exit_r=3.0),
                 BoxParams(exit_kind="none")]

STAGE_B_ENTRIES = [
    (BoxParams(leverage=3, min_stop_pct=0.6, trigger="color_only"), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, long_stop="prev_candle"), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, near_frac=0.05), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, near_frac=0.20), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, allow_outside=False), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, allow_short=False), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6, allow_long=False), 1),
    (BoxParams(leverage=3, min_stop_pct=0.6), 3),      # ÜRETİM TEMPOSU: 15 dk'da bir karar
]


def verdict(meta: dict, *, min_trades: int = 30) -> list[dict]:
    """Önceden yazılmış karar kuralını uygular. 'Kazanan' seçmez; geçen kolu İŞARETLER."""
    by_arm: dict[str, list[dict]] = {}
    for r in meta["results"]:
        by_arm.setdefault(r["arm"].rsplit("|s", 1)[0] + "|s%d" % r["stride"], []).append(r)
    out = []
    for arm, rows in by_arm.items():
        wins = {r["window"]: r for r in rows}
        if len(wins) < 3:
            continue
        ok = all(w["stats"].get("n", 0) >= min_trades and w["stats"].get("ci95_lo", -9) > 0
                 for w in wins.values())
        out.append({"arm": arm, "passes_all_three": bool(ok),
                    "per_window": {k: {"n": v["stats"].get("n", 0),
                                       "mean_r": v["stats"].get("mean_r"),
                                       "ci95_lo": v["stats"].get("ci95_lo"),
                                       "mean_r_gross": v["stats"].get("mean_r_gross")}
                                   for k, v in wins.items()}})
    return sorted(out, key=lambda x: (not x["passes_all_three"], x["arm"]))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "A"
    entries = STAGE_A_ENTRIES if stage == "A" else STAGE_B_ENTRIES
    t0 = time.time()
    print("AŞAMA %s — %d giriş ailesi x %d çıkış x 3 pencere" % (stage, len(entries), len(STAGE_A_EXITS)))
    meta = B.run(entries, STAGE_A_EXITS, tag="v15_%s" % stage.lower())
    v = verdict(meta)
    passed = [x for x in v if x["passes_all_three"]]
    print("\nsüre %.0f sn · kol %d · ÜÇ PENCEREDE DE GEÇEN: %d" % (time.time() - t0, len(v), len(passed)))
    for x in passed[:10]:
        print("  GEÇTİ %s  %s" % (x["arm"], x["per_window"]))
    if not passed:
        best = sorted(v, key=lambda x: -min(w["mean_r"] or -9 for w in x["per_window"].values()))[:5]
        print("  (hiçbiri geçmedi; en kötü penceresi en iyi olan 5 kol:)")
        for x in best:
            print("   %s" % x["arm"])
            for k, w in sorted(x["per_window"].items()):
                print("     %s n=%-5s netR=%+8.4f  brutR=%+8.4f" % (k, w["n"], w["mean_r"] or 0, w["mean_r_gross"] or 0))
    print("\nrapor: %s" % (B.OUT / ("BOX_SWEEP_v15_%s.md" % stage.lower())))
