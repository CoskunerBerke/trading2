# -*- coding: utf-8 -*-
"""Sabirli (legacy) motorun ONAYLADIGI girisler vs ONAYLAMADIGI girisler."""
import os, sys, statistics as st, collections
os.chdir(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ".")
import analyze

RID = sys.argv[1] if len(sys.argv) > 1 else "split_ungated"
tr, _ = analyze.load_full(RID)
# plan kaynagi islem kaydindaki features icinde
import json, io
d = json.load(io.open("state/replay/%s/futures_ledger.json" % RID, encoding="utf-8"))
rows = []
for r in d["history"]:
    f = r.get("features") or {}
    rows.append({"src": f.get("plan_source") or "?", "rej": (f.get("legacy_reject") or "-").split(":")[0],
                 "r": float(r["r_multiple"]), "net": float(r["net_pnl"]), "sym": r["symbol"],
                 "side": r["side"], "exit": r["exit_reason"]})

def blok(sub, ad):
    if not sub:
        print("  %-26s islem yok" % ad); return None
    R = [x["r"] for x in sub]; N = [x["net"] for x in sub]
    w = [x for x in R if x > 0]; l = [abs(x) for x in R if x <= 0]
    be = st.mean(l) / (st.mean(w) + st.mean(l)) if w and l else None
    pf = (sum(x for x in N if x > 0) / -sum(x for x in N if x <= 0)) if any(x <= 0 for x in N) else None
    print("  %-26s n=%3d  ortR=%+.4f  isabet=%5.1f%%  net=%+8.2f  PF=%s  basabas=%s" % (
        ad, len(sub), st.mean(R), 100 * len(w) / len(sub), sum(N),
        ("%.3f" % pf) if pf else "-", ("%.1f%%" % (100 * be)) if be else "-"))
    return st.mean(R)

print("=== %s — TOPLAM n=%d ===" % (RID, len(rows)))
print()
print("SABIRLI MOTOR NE DEDI:")
a = blok([x for x in rows if x["src"] == "legacy"], "ONAYLADI (seviye plani)")
b = blok([x for x in rows if x["src"] == "atr"], "ONAYLAMADI (piyasadan)")
print()
print("ONAYLAMAMA SEBEBINE GORE:")
for k in sorted({x["rej"] for x in rows if x["src"] == "atr"}):
    blok([x for x in rows if x["src"] == "atr" and x["rej"] == k], k)
if a is not None and b is not None:
    print()
    print("FARK (onayli - onaysiz): %+.4f R/islem" % (a - b))
    la = [x for x in rows if x["src"] == "legacy"]; lb = [x for x in rows if x["src"] == "atr"]
    ta = [{"r": x["r"], "symbol": x["sym"], "month": "x"} for x in la]
    tb = [{"r": x["r"], "symbol": x["sym"], "month": "x"} for x in lb]
    print("  (blok bootstrap icin ay bilgisi bu kesitte yok; fark NOKTA tahminidir)")
print()
print("CIKIS DAGILIMI:")
for src in ("legacy", "atr"):
    c = collections.Counter(x["exit"] for x in rows if x["src"] == src)
    print("  %-8s %s" % (src, dict(c)))
