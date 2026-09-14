# -*- coding: utf-8 -*-
"""PROTOCOL_V4 sonuç üreticisi. Ölçütler ve eşikler PROTOCOL_V4.md §5-6'dan; sonuç görülmeden yazıldı.

Girdi : out/meta_v4_*.json, state/replay/v4_*/futures_ledger.json, out/meta_v3_a1_*.json (hash)
Çıktı : out/v4_sonuc.json, out/DENEY_V4.md
"""
import io
import json
import statistics as st
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
ARMS = [("c0", "C0 temel (tetikli, kuralsız)"), ("c1", "C1 4h şekil"),
        ("c2", "C2 4h şekil + teyit"), ("c3", "C3 karşı-şekil vetosu"), ("c4", "C4 1d şekil")]
WINS = [("p1", "2020-11-01", "2022-08-31"), ("p2", "2022-09-01", "2024-08-31")]
START = 100.0


def fmt_pct(x):
    return ("%+.2f%%" % x).replace(".", ",")


def fmt_r(x):
    return "—" if x is None else ("%+.4f" % x).replace(".", ",")


def fmt_ci(t):
    if not t or t[1] is None:
        return "—"
    return "[%s, %s]" % (fmt_r(t[1]), fmt_r(t[2]))


def load_run(rid):
    meta = json.load(io.open(OUT / ("meta_%s.json" % rid), encoding="utf-8"))
    tr, led = A.load_full(rid)
    return meta, tr


def stressed_net(tr):
    """Sabit işlem kümesinde ücret ×2 + slippage ×2: her ikisi BİR kez daha düşülür."""
    return sum(t["net"] - t["fees"] - t["slip"] for t in tr)


def arm_row(rid):
    meta, tr = load_run(rid)
    m = A.metrics(tr)
    net = m.get("net_usdt", 0.0) if tr else 0.0
    return {"run_id": rid, "n": len(tr), "net_pct": net / START * 100.0,
            "stress_pct": (stressed_net(tr) / START * 100.0) if tr else 0.0,
            "mean_r": m.get("mean_r"), "median_r": m.get("median_r"),
            "win_rate": m.get("win_rate"), "pf": m.get("profit_factor"),
            "max_dd_pct": (m.get("max_drawdown_usdt") or 0.0) / START * 100.0,
            "symbols": m.get("symbols", 0), "months": m.get("months", 0),
            "n_decisions": meta.get("n_decisions"), "n_actionable": meta.get("n_actionable"),
            "n_opened": meta.get("n_opened"), "hash": meta.get("determinism_hash"),
            "rejections": (meta.get("rejections") or {}).get("by_reason", {}),
            "wall_s": meta.get("wall_s") or meta.get("elapsed_s"), "trades": tr}


def criteria(row, base):
    c1 = row["net_pct"] > 0
    c2 = row["net_pct"] > base["net_pct"]
    c3 = row["n"] >= 40 and row["symbols"] >= 5
    c4 = row["stress_pct"] > 0
    return {"1_pozitif": c1, "2_temelden_iyi": c2, "3_n40_coin5": c3, "4_stres_pozitif": c4,
            "hepsi": c1 and c2 and c3 and c4}


def main():
    rows = {}
    for a, _ in ARMS:
        for p, _, _ in WINS:
            rows[(a, p)] = arm_row("v4_%s_%s" % (a, p))
    # C0 hash paritesi (protokol §3): v3_a1 ile aynı olmalı
    parity = {}
    for p, _, _ in WINS:
        v3 = json.load(io.open(OUT / ("meta_v3_a1_%s.json" % p), encoding="utf-8"))
        parity[p] = {"v3_a1": v3.get("determinism_hash"), "v4_c0": rows[("c0", p)]["hash"],
                     "same": v3.get("determinism_hash") == rows[("c0", p)]["hash"]}
    # ölçütler + belirsizlik
    verdict, ci = {}, {}
    for a, _ in ARMS[1:]:
        per = {}
        for p, _, _ in WINS:
            per[p] = criteria(rows[(a, p)], rows[("c0", p)])
            tr_a, tr_0 = rows[(a, p)]["trades"], rows[("c0", p)]["trades"]
            ci[(a, p)] = {"observed": A.observed_diff(tr_a, tr_0),
                          "boot": A.boot_diff(tr_a, tr_0, paired=True)}
        both = per["p1"]["hepsi"] and per["p2"]["hepsi"]
        one = per["p1"]["hepsi"] or per["p2"]["hepsi"]
        verdict[a] = {"per_window": per,
                      "karar": ("GÖLGE PAPER ADAYI" if both else
                                "tek pencerede olumlu, DOĞRULANMADI" if one else "REDDEDİLDİ")}
    # JSON
    js = {"parity": parity,
          "rows": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                   for k, v in rows.items()},
          "diff_vs_c0": {"%s_%s" % k: v for k, v in ci.items()}, "verdict": verdict}
    io.open(OUT / "v4_sonuc.json", "w", encoding="utf-8").write(
        json.dumps(js, ensure_ascii=False, indent=1, default=str))
    # Markdown
    L = ["# DENEY V4 — mum formasyonu giriş onayı", "",
         "Protokol: `PROTOCOL_V4.md` (sonuçlar görülmeden yazıldı). Kod: `out/v4_code.patch`.",
         "Kollar tetikli (= V3 A1). 5 kol × 2 pencere = 10 koşu; başka koşu YAPILMADI.", "",
         "## 0. C0 hash paritesi (v3_a1 ile)", "", "| pencere | v3_a1 | v4_c0 | aynı |", "|---|---|---|---|"]
    for p, _, _ in WINS:
        q = parity[p]
        L.append("| %s | `%s` | `%s` | %s |" % (p.upper(), (q["v3_a1"] or "")[:12], (q["v4_c0"] or "")[:12],
                                             "✓" if q["same"] else "**✗ FARKLI**"))
    L += ["", "## 1. Sonuçlar", "",
          "| pencere | kol | işlem | hesap getirisi | stresli | ort net R | medyan R | isabet | PF | maks düşüş | coin |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p, f, t in WINS:
        for a, name in ARMS:
            r = rows[(a, p)]
            L.append("| %s %s→%s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %d |" % (
                p.upper(), f[:7], t[:7], name, r["n"], fmt_pct(r["net_pct"]), fmt_pct(r["stress_pct"]),
                fmt_r(r["mean_r"]), fmt_r(r["median_r"]),
                ("%%%.1f" % (100 * r["win_rate"])).replace(".", ",") if r["win_rate"] is not None else "—",
                ("%.3f" % r["pf"]).replace(".", ",") if r["pf"] else "—",
                ("%.2f%%" % r["max_dd_pct"]).replace(".", ","), r["symbols"]))
    L += ["", "## 2. Kabul ölçütleri (önceden yazılı, değiştirilmedi)", "",
          "| kol | P1 (1/2/3/4) | P2 (1/2/3/4) | karar |", "|---|---|---|---|"]
    for a, name in ARMS[1:]:
        v = verdict[a]
        def cell(p):
            c = v["per_window"][p]
            return " ".join("✓" if c[k] else "✗" for k in ("1_pozitif", "2_temelden_iyi", "3_n40_coin5", "4_stres_pozitif"))
        L.append("| %s | %s | %s | **%s** |" % (name, cell("p1"), cell("p2"), v["karar"]))
    L += ["", "## 3. Belirsizlik (işlem başına ort R farkı, kol − C0; eşli blok bootstrap coin×ay, 2000)", "",
          "| karşılaştırma | pencere | gözlenen | %95 aralık |", "|---|---|---|---|"]
    for a, name in ARMS[1:]:
        for p, _, _ in WINS:
            c = ci[(a, p)]
            L.append("| %s − C0 | %s | %s | %s |" % (a.upper(), p.upper(), fmt_r(c["observed"]), fmt_ci(c["boot"])))
    L += ["", "## 4. Ret dökümü (kural kaynaklı retler)", "", "| koşu | karar | uygulanabilir | açılan | kural retleri |", "|---|---|---|---|---|"]
    for p, _, _ in WINS:
        for a, name in ARMS:
            r = rows[(a, p)]
            rule_rej = {k: v for k, v in r["rejections"].items() if k[:3] in ("C1_", "C2_", "C3_")}
            L.append("| %s | %s | %s | %s | %s |" % (r["run_id"], r["n_decisions"], r["n_actionable"], r["n_opened"],
                                                    ", ".join("%s=%s" % kv for kv in sorted(rule_rej.items(), key=lambda kv: -kv[1])) or "—"))
    L += ["", "## 5. Yorum", "", "_(sonuçlar görüldükten sonra elle yazılır; sayılar yukarıdaki tablolardan alınır)_", ""]
    io.open(OUT / "DENEY_V4.md", "w", encoding="utf-8").write("\n".join(L))
    print(json.dumps({"parity": parity, "verdict": {a: v["karar"] for a, v in verdict.items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
