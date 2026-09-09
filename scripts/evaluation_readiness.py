#!/usr/bin/env python3
"""DEGERLENDIRME HAZIRLIGI — "yeterli veri birikti mi?" sorusunu tek komutla yanitlar.

Neden bu var: 2026-09-10 olcumu, 29 kapanmis islemin bu sistemin para kaybettigini ISTATISTIKSEL
OLARAK GOSTERMEDIGINI buldu. Beklenti -0.357R, ama %95 araligi [-0.852, +0.139] ve kazanma orani
araligi [%12.2, %42.1] basa-bas orani %36.1'i ICERIYOR. Yani baglayici kisit yeni bir kural degil,
TAKVIM ZAMANIDIR. Bu betik o zamani olcer ve ne zaman gercek bir degerlendirmenin anlamli olacagini
soyler.

AG GEREKTIRMEZ, hicbir seye YAZMAZ (rapor dosyasi haric), hicbir karari etkilemez.

Kullanim:
    python scripts/evaluation_readiness.py [--state DIR] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEFAULT_STATE = "/opt/tradingbot/data/state"
#: Kabul kapisi: aralik SIFIRI disladiginda beklenti hakkinda konusulabilir.
TARGET_CI_EXCLUDES_ZERO = True


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Kazanma orani icin Wilson araligi — kucuk n'de normal yaklasimdan durust."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--json", default=None, help="ozet JSON cikti yolu (istege bagli)")
    a = ap.parse_args()
    st = Path(a.state)

    led_p = st / "futures_ledger.json"
    if not led_p.exists():
        print(f"defter bulunamadi: {led_p}", file=sys.stderr)
        return 2
    led = json.loads(io.open(led_p, encoding="utf-8").read())
    hist = led.get("history") or []
    n = len(hist)
    if n == 0:
        print("kapanmis islem YOK — degerlendirme icin veri yok.")
        return 0

    rs = [float(t["r_multiple"]) for t in hist]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    mean_r = sum(rs) / n
    sd = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    lo, hi = mean_r - 1.96 * se, mean_r + 1.96 * se
    w_lo, w_hi = wilson(len(wins), n)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 1.0
    breakeven = al / (aw + al) if (aw + al) > 0 else float("nan")

    # Bu nokta kestirimi korunursa aralik sifiri hangi n'de dislar?  n* = (1.96*sd/|mean|)^2
    need = int(math.ceil((1.96 * sd / abs(mean_r)) ** 2)) if mean_r and sd else 0
    remaining = max(0, need - n)

    first = min(t["closed_at"] for t in hist)
    last = max(t["closed_at"] for t in hist)
    d0 = datetime.fromisoformat(first)
    d1 = datetime.fromisoformat(last)
    days = max((d1 - d0).total_seconds() / 86400.0, 1e-9)
    per_day = n / days

    # SON 7 GUNUN temposu: kapi sirasi onarimindan (1c4cba1) sonra yeni giris sayisi cok dustu.
    # Genel ortalama tempo, GECMIS tempodur ve gelecege dogrudan yansitilamaz.
    cut = d1.timestamp() - 7 * 86400
    recent = [t for t in hist if datetime.fromisoformat(t["closed_at"]).timestamp() >= cut]
    per_day_recent = len(recent) / 7.0

    snap = st / "entry_snapshot.jsonl"
    cand_lines = 0
    if snap.exists():
        with io.open(snap, encoding="utf-8", errors="replace") as fh:
            for _ in fh:                                   # akisla say; dosya 90 MB+ olabilir
                cand_lines += 1

    print(f"kapanmis islem     : {n}  ({first[:10]} -> {last[:10]}, {days:.1f} gun, "
          f"{per_day:.2f} kapanis/gun)")
    print(f"beklenti           : {mean_r:+.4f} R   %95 aralik [{lo:+.3f}, {hi:+.3f}]"
          f"   {'SIFIRI DISLIYOR' if (lo > 0 or hi < 0) else 'SIFIRI ICERIYOR'}")
    print(f"kazanma orani      : {len(wins)}/{n} = %{len(wins)/n*100:.1f}   "
          f"Wilson [%{w_lo*100:.1f}, %{w_hi*100:.1f}]")
    print(f"basa-bas orani     : %{breakeven*100:.1f}   (ort. kazanc {aw:.3f}R / ort. kayip {al:.3f}R)"
          f"   {'ARALIGIN DISINDA' if not (w_lo <= breakeven <= w_hi) else 'ARALIGIN ICINDE'}")
    print(f"son 7 gun          : {len(recent)} kapanis = {per_day_recent:.2f}/gun"
          f"   (genel tempo {per_day:.2f}/gun)")
    print(f"aday kaydi         : {cand_lines:,} satir ({snap.name})")
    print()
    if lo > 0 or hi < 0:
        print(" · KARAR VERILEBILIR: beklenti araligi sifiri disliyor.")
    else:
        eta = remaining / per_day if per_day > 0 else float("inf")
        eta_r = remaining / per_day_recent if per_day_recent > 0 else float("inf")
        print(f" · HENUZ KARAR VERILEMEZ. Ayni nokta kestirimi surerse aralik sifiri {need} kapanista")
        print(f"   dislar; {remaining} kapanis daha gerekir ≈ {eta:.0f} gun GENEL tempoda,"
              + (f" ≈ {eta_r:.0f} gun son 7 gunun temposunda." if per_day_recent > 0
                 else " son 7 gunde HIC kapanis yok -> bu tempoda ASLA."))
        if per_day_recent < per_day / 2:
            print("   UYARI: son tempo genel temponun yarisindan dusuk. Kapi sirasi onarimindan sonra")
            print("   yeni giris sayisi kasten azaldi; 'X gun sonra karar veririz' PLANLANAMAZ.")
        if not (w_lo <= breakeven <= w_hi):
            print("   (kazanma orani araligi basa-bas oranini DISLIYOR — bu tek basina yeterli DEGIL,")
            print("    beklenti araligi da sifiri dislamalidir.)")
        else:
            print("   Kazanma orani araligi basa-bas oranini da iceriyor: sistemin kaybettigi")
            print("   ISTATISTIKSEL OLARAK GOSTERILMIS DEGILDIR.")
    print()
    print(" · Bu betik hicbir seyi degistirmez. Gercek degerlendirme icin:")
    print("     scripts/counterfactual_run.py -> scripts/challenger_eval.py -> scripts/challenger_verdict.py")
    print("   Kabul olcutleri `docs/CHALLENGERS_V1.md` icinde ONCEDEN yazilidir.")

    if a.json:
        out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "n_closed": n, "mean_r": round(mean_r, 6), "ci95": [round(lo, 6), round(hi, 6)],
               "wins": len(wins), "win_rate": round(len(wins) / n, 6),
               "win_rate_wilson": [round(w_lo, 6), round(w_hi, 6)],
               "breakeven_rate": round(breakeven, 6), "avg_win_r": round(aw, 6), "avg_loss_r": round(al, 6),
               "closes_per_day": round(per_day, 6), "closes_per_day_last7": round(per_day_recent, 6), "n_needed_for_ci_excl_zero": need,
               "closes_remaining": remaining, "candidate_lines": cand_lines,
               "decidable": bool(lo > 0 or hi < 0)}
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        io.open(a.json, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nJSON: {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
