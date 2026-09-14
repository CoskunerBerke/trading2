# -*- coding: utf-8 -*-
"""PROTOCOL_V11 sonuç üreticisi: M1/M2/M3 vs T2 (v9_t2_*), üç pencere, 5 şart (§2). Sonuç görülmeden yazıldı."""
import datetime as _dt
import io
import json
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
ROWS = [("t2", "T2 EMA200 (temel, canlı kâğıt defter)"), ("m1", "M1 EMA100"), ("m2", "M2 TSMOM 28 gün"), ("m3", "M3 TSMOM 91 gün")]
WINS = [("p1", "2020-11-01", "2022-08-31"), ("p2", "2022-09-01", "2024-08-31"), ("p3", "2024-09-01", "2026-08-31")]
START = 100.0


def rid_of(arm, win):
    return "v9_t2_%s" % win if arm == "t2" else "v11_%s_%s" % (arm, win)


def fmt_pct(x): return ("%+.2f%%" % x).replace(".", ",")
def fmt_r(x): return "—" if x is None else ("%+.4f" % x).replace(".", ",")


def row(rid):
    meta = json.load(io.open(OUT / ("meta_%s.json" % rid), encoding="utf-8"))
    tr, _ = A.load_full(rid)
    m = A.metrics(tr)
    net = m.get("net_usdt", 0.0) if tr else 0.0
    return {"run_id": rid, "n": len(tr), "net_pct": net / START * 100.0,
            "stress_pct": (sum(x["net"] - x["fees"] - x["slip"] for x in tr) / START * 100.0) if tr else 0.0,
            "mean_r": m.get("mean_r"), "win_rate": m.get("win_rate"), "pf": m.get("profit_factor"),
            "max_dd_pct": (m.get("max_drawdown_usdt") or 0.0) / START * 100.0, "symbols": m.get("symbols", 0),
            "mean_bars": m.get("mean_bars_held"), "fees": m.get("fees_total", 0.0), "funding": m.get("funding_total", 0.0),
            "boot": A.boot_mean_r(tr) if tr else (None, None, None), "rejections": (meta.get("rejections") or {}).get("by_reason", {})}


def main():
    R = {(a, p): row(rid_of(a, p)) for a, _ in ROWS for p, _, _ in WINS}
    t2_worst_dd = max(R[("t2", p)]["max_dd_pct"] for p, _, _ in WINS)
    verdict = {}
    for a, _ in ROWS[1:]:
        per = {}
        worst = max(R[(a, p)]["max_dd_pct"] for p, _, _ in WINS)
        for p, _, _ in WINS:
            r, b = R[(a, p)], R[("t2", p)]
            c = {"1_pozitif": r["net_pct"] > 0, "2_T2den_iyi": r["net_pct"] > b["net_pct"],
                 "3_n20_coin5": r["n"] >= 20 and r["symbols"] >= 5, "4_stres_pozitif": r["stress_pct"] > 0,
                 "5_dd_T2yi_asmaz": worst <= t2_worst_dd}
            c["hepsi"] = all(c.values()); per[p] = c
        verdict[a] = {"per_window": per, "worst_dd": worst,
                      "karar": "T2'NİN YERİNE ADAY" if all(per[p]["hepsi"] for p, _, _ in WINS)
                      else ("bazı pencerelerde T2'den iyi, DOĞRULANMADI" if any(per[p]["hepsi"] for p, _, _ in WINS) else "REDDEDİLDİ")}
    io.open(OUT / "v11_sonuc.json", "w", encoding="utf-8").write(json.dumps(
        {"rows": {"%s_%s" % k: v for k, v in R.items()}, "verdict": verdict, "t2_worst_dd": t2_worst_dd}, ensure_ascii=False, indent=1, default=str))
    L = ["# DENEY V11 — momentum tanımı: EMA200'ün alternatifleri", "",
         "Protokol: `PROTOCOL_V11.md` (sonuçlar görülmeden yazıldı). Strateji modu, botun defteri, funding dahil. T2 satırları `v9_t2_*`.", "",
         "## 1. Sonuçlar", "",
         "| pencere | kol | işlem | hesap getirisi | stresli | ort net R | %95 (ort R) | isabet | PF | maks düşüş | ort bar | ücret | funding |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p, f, t in WINS:
        for a, name in ROWS:
            r = R[(a, p)]; b = r["boot"]
            ci = "[%s, %s]" % (fmt_r(b[1]), fmt_r(b[2])) if b and b[1] is not None else "—"
            L.append("| %s %s→%s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %.1f | %.1f |" % (
                p.upper(), f[:7], t[:7], name, r["n"], fmt_pct(r["net_pct"]), fmt_pct(r["stress_pct"]), fmt_r(r["mean_r"]), ci,
                ("%%%.1f" % (100 * r["win_rate"])).replace(".", ",") if r["win_rate"] is not None else "—",
                ("%.3f" % r["pf"]).replace(".", ",") if r["pf"] else "—", ("%.1f%%" % r["max_dd_pct"]).replace(".", ","),
                r["mean_bars"] if r["mean_bars"] is not None else "—", r["fees"], r["funding"]))
    L += ["", "## 2. Kabul (§2; üç pencerede de; T2 en kötü düşüş %s)" % ("%.1f%%" % t2_worst_dd).replace(".", ","), "",
          "| kol | P1 (1/2/3/4/5) | P2 | P3 | en kötü düşüş | karar |", "|---|---|---|---|---|---|"]
    keys = ("1_pozitif", "2_T2den_iyi", "3_n20_coin5", "4_stres_pozitif", "5_dd_T2yi_asmaz")
    for a, name in ROWS[1:]:
        v = verdict[a]
        cell = lambda p: " ".join("✓" if v["per_window"][p][k] else "✗" for k in keys)
        L.append("| %s | %s | %s | %s | %s | **%s** |" % (name, cell("p1"), cell("p2"), cell("p3"), ("%.1f%%" % v["worst_dd"]).replace(".", ","), v["karar"]))
    L += ["", "## 3. Yorum", "", "_(sonuçlar görüldükten sonra elle yazılır)_", ""]
    io.open(OUT / "DENEY_V11.md", "w", encoding="utf-8").write("\n".join(L))
    print(json.dumps({"verdict": {a: v["karar"] for a, v in verdict.items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
