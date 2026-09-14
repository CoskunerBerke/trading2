# -*- coding: utf-8 -*-
"""PROTOCOL_V9 sonuç üreticisi: T1/T2/T3 vs B0 (eski üretim) ve R1 (şu anki üretim), üç pencere.
Kabul (§4): üç pencerede de (1) pozitif, (2) R1'den yüksek, (3) ≥20 işlem ve ≥5 coin, (4) stres altında pozitif."""
import datetime as _dt
import io
import json
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
ROWS = [("b0", "B0 eski üretim (tetik + mum vetosu)"), ("r1", "R1 şu anki üretim (+ rejim kapısı)"),
        ("t1", "T1 saf EMA200 trend"), ("t2", "T2 trend + BTC rejimi"), ("t3", "T3 trend + botun çıkışı")]
WINS = [("p1", "2020-11-01", "2022-08-31"), ("p2", "2022-09-01", "2024-08-31"), ("p3", "2024-09-01", "2026-08-31")]
START = 100.0


def rid_of(arm, win):
    if arm == "b0":
        return "v6_b0_p3" if win == "p3" else "v4_c3_%s" % win
    if arm == "r1":
        return "v7_r1_%s" % win
    return "v9_%s_%s" % (arm, win)


def fmt_pct(x): return ("%+.2f%%" % x).replace(".", ",")
def fmt_r(x): return "—" if x is None else ("%+.4f" % x).replace(".", ",")


def _days(f, t):
    return (_dt.date.fromisoformat(t) - _dt.date.fromisoformat(f)).days + 1


def exposure_days(tr, f, t):
    """Pozisyonda geçen gün payı (birleşik: herhangi bir coin açıkken)."""
    d0, d1 = _dt.date.fromisoformat(f), _dt.date.fromisoformat(t)
    days = set()
    for x in tr:
        a = _dt.datetime.fromisoformat(x["opened_at"].replace("Z", "+00:00")).date()
        b = _dt.datetime.fromisoformat(x["closed_at"].replace("Z", "+00:00")).date()
        cur = max(a, d0)
        while cur <= min(b, d1):
            days.add(cur); cur += _dt.timedelta(days=1)
    return len(days) / _days(f, t)


def row(rid, f, t):
    meta = json.load(io.open(OUT / ("meta_%s.json" % rid), encoding="utf-8"))
    tr, _ = A.load_full(rid)
    m = A.metrics(tr)
    net = m.get("net_usdt", 0.0) if tr else 0.0
    return {"run_id": rid, "n": len(tr), "net_pct": net / START * 100.0,
            "stress_pct": (sum(x["net"] - x["fees"] - x["slip"] for x in tr) / START * 100.0) if tr else 0.0,
            "mean_r": m.get("mean_r"), "win_rate": m.get("win_rate"), "pf": m.get("profit_factor"),
            "max_dd_pct": (m.get("max_drawdown_usdt") or 0.0) / START * 100.0, "symbols": m.get("symbols", 0),
            "fees": m.get("fees_total", 0.0), "funding": m.get("funding_total", 0.0),
            "exposure": exposure_days(tr, f, t) if tr else 0.0,
            "exits": {k: sum(1 for x in tr if x["exit"] == k) for k in sorted(set(x["exit"] for x in tr))} if tr else {},
            "mean_bars": m.get("mean_bars_held"), "rejections": (meta.get("rejections") or {}).get("by_reason", {})}


def main():
    R = {(a, p): row(rid_of(a, p), f, t) for a, _ in ROWS for p, f, t in WINS}
    verdict = {}
    for a, _ in ROWS[2:]:
        per = {}
        for p, _, _ in WINS:
            r, base = R[(a, p)], R[("r1", p)]
            c = {"1_pozitif": r["net_pct"] > 0, "2_R1den_iyi": r["net_pct"] > base["net_pct"],
                 "3_n20_coin5": r["n"] >= 20 and r["symbols"] >= 5, "4_stres_pozitif": r["stress_pct"] > 0}
            c["hepsi"] = all(c.values()); per[p] = c
        verdict[a] = {"per_window": per, "karar": "SADELEŞTİRME ADAYI" if all(per[p]["hepsi"] for p, _, _ in WINS)
                      else ("bazı pencerelerde olumlu, DOĞRULANMADI" if any(per[p]["hepsi"] for p, _, _ in WINS) else "REDDEDİLDİ")}
    io.open(OUT / "v9_sonuc.json", "w", encoding="utf-8").write(json.dumps(
        {"rows": {"%s_%s" % k: v for k, v in R.items()}, "verdict": verdict}, ensure_ascii=False, indent=1, default=str))
    L = ["# DENEY V9 — sadeleştirme: tek kurallı trend, botun kendi defterinde", "",
         "Protokol: `PROTOCOL_V9.md` (sonuçlar görülmeden yazıldı). Strateji modu: uzman yığını yok; defter, risk motoru,",
         "borsa filtreleri, kayma ve arşiv funding üretimle aynı. B0 ve R1 satırları önceki koşulardan.", "",
         "## 1. Sonuçlar", "",
         "| pencere | kol | işlem | hesap getirisi | stresli | ort net R | isabet | PF | maks düşüş | maruziyet (gün) | ort bar | ücret | funding |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p, f, t in WINS:
        for a, name in ROWS:
            r = R[(a, p)]
            L.append("| %s %s→%s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %.1f | %.1f |" % (
                p.upper(), f[:7], t[:7], name, r["n"], fmt_pct(r["net_pct"]), fmt_pct(r["stress_pct"]), fmt_r(r["mean_r"]),
                ("%%%.1f" % (100 * r["win_rate"])).replace(".", ",") if r["win_rate"] is not None else "—",
                ("%.3f" % r["pf"]).replace(".", ",") if r["pf"] else "—", ("%.1f%%" % r["max_dd_pct"]).replace(".", ","),
                ("%%%.0f" % (100 * r["exposure"])), r["mean_bars"] if r["mean_bars"] is not None else "—", r["fees"], r["funding"]))
    L += ["", "## 2. Kabul ölçütleri (§4; üç pencerede de)", "", "| kol | P1 (1/2/3/4) | P2 | P3 | karar |", "|---|---|---|---|---|"]
    keys = ("1_pozitif", "2_R1den_iyi", "3_n20_coin5", "4_stres_pozitif")
    for a, name in ROWS[2:]:
        v = verdict[a]
        cell = lambda p: " ".join("✓" if v["per_window"][p][k] else "✗" for k in keys)
        L.append("| %s | %s | %s | %s | **%s** |" % (name, cell("p1"), cell("p2"), cell("p3"), v["karar"]))
    L += ["", "## 3. Çıkış nedenleri ve retler", "", "| koşu | çıkışlar | retler (ilk 4) |", "|---|---|---|"]
    for p, _, _ in WINS:
        for a, _ in ROWS[2:]:
            r = R[(a, p)]
            rr = sorted(r["rejections"].items(), key=lambda kv: -kv[1])[:4]
            L.append("| %s | %s | %s |" % (r["run_id"], ", ".join("%s=%d" % kv for kv in r["exits"].items()), ", ".join("%s=%d" % kv for kv in rr) or "—"))
    L += ["", "## 4. Yorum", "", "_(sonuçlar görüldükten sonra elle yazılır)_", ""]
    io.open(OUT / "DENEY_V9.md", "w", encoding="utf-8").write("\n".join(L))
    print(json.dumps({"verdict": {a: v["karar"] for a, v in verdict.items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
