# -*- coding: utf-8 -*-
"""PROTOCOL_V6 sonuç üreticisi (3 pencere, 5 şart, üçünde de geçmeli). Sonuç görülmeden yazıldı."""
import io
import json
import statistics as st
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
ARMS = [("b0", "B0 temel (üretim)"), ("s1", "S1 yalnız 12-20 UTC"), ("s2", "S2 hafta sonu yok"),
        ("s3", "S3 ikisi birlikte")]
WINS = [("p1", "2020-11-01", "2022-08-31"), ("p2", "2022-09-01", "2024-08-31"), ("p3", "2024-09-01", "2026-08-31")]
START = 100.0


def rid_of(arm, win):
    if arm == "b0":
        return "v6_b0_p3" if win == "p3" else "v4_c3_%s" % win
    return "v6_%s_%s" % (arm, win)


def fmt_pct(x): return ("%+.2f%%" % x).replace(".", ",")
def fmt_r(x): return "—" if x is None else ("%+.4f" % x).replace(".", ",")
def fmt_ci(t): return "—" if (not t or t[1] is None) else "[%s, %s]" % (fmt_r(t[1]), fmt_r(t[2]))


def arm_row(rid):
    meta = json.load(io.open(OUT / ("meta_%s.json" % rid), encoding="utf-8"))
    tr, _ = A.load_full(rid)
    m = A.metrics(tr)
    net = m.get("net_usdt", 0.0) if tr else 0.0
    return {"run_id": rid, "n": len(tr), "net_pct": net / START * 100.0,
            "stress_pct": (sum(t["net"] - t["fees"] - t["slip"] for t in tr) / START * 100.0) if tr else 0.0,
            "mean_r": m.get("mean_r"), "median_r": m.get("median_r"), "win_rate": m.get("win_rate"),
            "pf": m.get("profit_factor"), "max_dd_pct": (m.get("max_drawdown_usdt") or 0.0) / START * 100.0,
            "symbols": m.get("symbols", 0), "n_decisions": meta.get("n_decisions"),
            "n_actionable": meta.get("n_actionable"), "n_opened": meta.get("n_opened"),
            "hash": meta.get("determinism_hash"), "rejections": (meta.get("rejections") or {}).get("by_reason", {}),
            "trades": tr}


def kept_removed(base_tr, arm_tr):
    key = lambda t: (t["symbol"], t["opened_at"])
    bk, ak = {key(t): t for t in base_tr}, {key(t): t for t in arm_tr}
    kept = [bk[k] for k in bk if k in ak]; rem = [bk[k] for k in bk if k not in ak]; new = [ak[k] for k in ak if k not in bk]
    mr = lambda xs: (st.mean(t["r"] for t in xs) if xs else None)
    return {"kept_n": len(kept), "kept_r": mr(kept), "removed_n": len(rem), "removed_r": mr(rem), "new_n": len(new), "new_r": mr(new)}


def criteria(row, base, kr):
    c1 = row["net_pct"] > 0; c2 = row["net_pct"] > base["net_pct"]
    c3 = row["n"] >= 40 and row["symbols"] >= 5; c4 = row["stress_pct"] > 0
    c5 = (kr["kept_r"] is not None and kr["removed_r"] is not None and kr["kept_r"] >= kr["removed_r"]) or (kr["removed_n"] == 0 and kr["kept_n"] > 0)
    return {"1_pozitif": c1, "2_temelden_iyi": c2, "3_n40_coin5": c3, "4_stres_pozitif": c4, "5_tutulan_ge_elenen": c5,
            "hepsi": c1 and c2 and c3 and c4 and c5}


def main():
    rows = {(a, p): arm_row(rid_of(a, p)) for a, _ in ARMS for p, _, _ in WINS}
    verdict, ci, kr_all = {}, {}, {}
    for a, _ in ARMS[1:]:
        per = {}
        for p, _, _ in WINS:
            kr = kept_removed(rows[("b0", p)]["trades"], rows[(a, p)]["trades"]); kr_all[(a, p)] = kr
            per[p] = criteria(rows[(a, p)], rows[("b0", p)], kr)
            ci[(a, p)] = {"observed": A.observed_diff(rows[(a, p)]["trades"], rows[("b0", p)]["trades"]),
                          "boot": A.boot_diff(rows[(a, p)]["trades"], rows[("b0", p)]["trades"], paired=True)}
        allw = all(per[p]["hepsi"] for p, _, _ in WINS)
        anyw = any(per[p]["hepsi"] for p, _, _ in WINS)
        p3_unmeasurable = rows[("b0", "p3")]["n"] < 40
        verdict[a] = {"per_window": per, "p3_unmeasurable": p3_unmeasurable,
                      "karar": ("GÖLGE PAPER ADAYI" if allw else
                                "yalnız kirli pencerede olumlu, DOĞRULANMADI" if anyw else "REDDEDİLDİ")}
    js = {"rows": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in rows.items()},
          "diff_vs_b0": {"%s_%s" % k: v for k, v in ci.items()}, "kept_removed": {"%s_%s" % k: v for k, v in kr_all.items()},
          "verdict": verdict}
    io.open(OUT / "v6_sonuc.json", "w", encoding="utf-8").write(json.dumps(js, ensure_ascii=False, indent=1, default=str))
    L = ["# DENEY V6 — seans ve hafta sonu kapısı", "",
         "Protokol: `PROTOCOL_V6.md` (sonuçlar görülmeden yazıldı). P1/P2 bu soru için KİRLİ (bölmeden doğdu); P3 temiz.",
         "Temel B0 = üretim (tetik + C3 mum vetosu). 3 kol × 3 pencere + B0 P3 = 10 koşu.", "",
         "## 1. Sonuçlar", "",
         "| pencere | kol | işlem | hesap getirisi | stresli | ort net R | medyan R | isabet | PF | maks düşüş | coin |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p, f, t in WINS:
        for a, name in ARMS:
            r = rows[(a, p)]
            L.append("| %s %s→%s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %d |" % (
                p.upper(), f[:7], t[:7], name, r["n"], fmt_pct(r["net_pct"]), fmt_pct(r["stress_pct"]), fmt_r(r["mean_r"]), fmt_r(r["median_r"]),
                ("%%%.1f" % (100 * r["win_rate"])).replace(".", ",") if r["win_rate"] is not None else "—",
                ("%.3f" % r["pf"]).replace(".", ",") if r["pf"] else "—", ("%.2f%%" % r["max_dd_pct"]).replace(".", ","), r["symbols"]))
    L += ["", "## 2. Kabul ölçütleri (5 şart; üç pencerede de sağlanmalı)", "",
          "| kol | P1 kirli | P2 kirli | P3 temiz | karar |", "|---|---|---|---|---|"]
    keys = ("1_pozitif", "2_temelden_iyi", "3_n40_coin5", "4_stres_pozitif", "5_tutulan_ge_elenen")
    for a, name in ARMS[1:]:
        v = verdict[a]
        cell = lambda p: " ".join("✓" if v["per_window"][p][k] else "✗" for k in keys)
        L.append("| %s | %s | %s | %s | **%s** |" % (name, cell("p1"), cell("p2"), cell("p3"), v["karar"]))
    if any(v["p3_unmeasurable"] for v in verdict.values()):
        L.append("\nNOT: P3 temelinde 40'tan az işlem var; P3 'ölçülemedi' sayılır, 'geçti' sayılmaz.")
    L += ["", "## 3. Tutulan / elenen / yeni işlemler", "",
          "| pencere | kol | tutulan n | tutulan ort R | elenen n | elenen ort R | yeni n | yeni ort R |", "|---|---|---|---|---|---|---|---|"]
    for p, _, _ in WINS:
        for a, _ in ARMS[1:]:
            k = kr_all[(a, p)]
            L.append("| %s | %s | %d | %s | %d | %s | %d | %s |" % (p.upper(), a.upper(), k["kept_n"], fmt_r(k["kept_r"]), k["removed_n"], fmt_r(k["removed_r"]), k["new_n"], fmt_r(k["new_r"])))
    L += ["", "## 4. Belirsizlik (ort R farkı, kol − B0; eşli blok bootstrap)", "", "| karşılaştırma | pencere | gözlenen | %95 aralık |", "|---|---|---|---|"]
    for a, _ in ARMS[1:]:
        for p, _, _ in WINS:
            c = ci[(a, p)]
            L.append("| %s − B0 | %s | %s | %s |" % (a.upper(), p.upper(), fmt_r(c["observed"]), fmt_ci(c["boot"])))
    L += ["", "## 5. Ret dökümü", "", "| koşu | karar | uygulanabilir | açılan | kural retleri |", "|---|---|---|---|---|"]
    for p, _, _ in WINS:
        for a, _ in ARMS:
            r = rows[(a, p)]
            rr = {k: v for k, v in r["rejections"].items() if k[:3] in ("S1_", "S2_", "S3_")}
            L.append("| %s | %s | %s | %s | %s |" % (r["run_id"], r["n_decisions"], r["n_actionable"], r["n_opened"], ", ".join("%s=%s" % kv for kv in sorted(rr.items(), key=lambda kv: -kv[1])) or "—"))
    L += ["", "## 6. Yorum", "", "_(sonuçlar görüldükten sonra elle yazılır)_", ""]
    io.open(OUT / "DENEY_V6.md", "w", encoding="utf-8").write("\n".join(L))
    print(json.dumps({"verdict": {a: v["karar"] for a, v in verdict.items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
