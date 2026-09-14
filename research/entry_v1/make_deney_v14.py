# -*- coding: utf-8 -*-
"""DENEY_V14.md — PROTOCOL_V14 bayraklari (S1 isaret degisimi, S2 yariya inis, S3 orneklem). Yorum eklemez."""
import io
import json
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
WINS = ("p1", "p2", "p3")
WIN_TXT = {"p1": "P1 2020-11→2022-08", "p2": "P2 2022-09→2024-08", "p3": "P3 2024-09→2026-08"}
BASE = {"t2": ("T2 EMA200", "v9_t2_%s"), "m2": ("M2 TSMOM 28g", "v11_m2_%s")}


def load(rid):
    try:
        tr, _ = A.load_full(rid)
    except FileNotFoundError:
        return None
    return tr


def pct(v):
    return "—" if v is None else ("%+.1f%%" % v)


lines = ["# DENEY V14 — hayatta kalma yanlılığı: Ekim-2020 evreni (BTC ETH LINK YFI BCH UNI BNB LTC XRP DOT)", "",
         "Kod `3501304`; yalnız araştırma paketi; ayrı önbellek (`wt-2020/data`). Aynı kural, aynı defter kuralları,",
         "aynı üç pencere; tek fark işlem evreni. Bugünkü evren tabanları: `v9_t2_*`, `v11_m2_*`.", "",
         "| pencere | kural | bugünkü evren | 2020 evreni | işlem (2020) | coin (2020) | maks düşüş (2020) | S1 | S2 | S3 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
res = {"rows": {}, "flags": {}, "missing": []}
for k, (name, base_fmt) in BASE.items():
    n1 = n2 = 0
    for p in WINS:
        base_tr, new_tr = load(base_fmt % p), load("v14_%s_%s" % (k, p))
        base = round(sum(t["net"] for t in base_tr), 2) if base_tr else None
        if new_tr is None:
            res["missing"].append("v14_%s_%s" % (k, p))
            lines.append("| %s | %s | %s | — | — | — | — | — | — | — |" % (WIN_TXT[p], name, pct(base)))
            continue
        m = A.metrics(new_tr) if new_tr else {"n": 0, "net_usdt": 0.0, "symbols": 0, "max_drawdown_usdt": 0.0}
        new = round(m.get("net_usdt", 0.0), 2)
        s3 = m["n"] < 10
        s1 = (not s3) and base is not None and base > 0 and new < 0
        s2 = (not s3) and base is not None and base > 0 and 0 <= new < base / 2.0
        n1 += int(s1); n2 += int(s2)
        by = {}
        for t in new_tr:
            by[t["symbol"]] = by.get(t["symbol"], 0.0) + t["net"]
        res["rows"]["%s_%s" % (k, p)] = {"base": base, "new": new, "n": m["n"], "symbols": m.get("symbols"),
                                        "max_dd": m.get("max_drawdown_usdt"), "S1": s1, "S2": s2, "S3": s3,
                                        "by_symbol": {s: round(v, 2) for s, v in sorted(by.items(), key=lambda x: -x[1])}}
        lines.append("| %s | %s | %s | %s | %d | %s | %s%% | %s | %s | %s |" % (
            WIN_TXT[p], name, pct(base), pct(new), m["n"], m.get("symbols"), m.get("max_drawdown_usdt"),
            "**EVET**" if s1 else "hayır", "**EVET**" if s2 else "hayır", "**EVET**" if s3 else "hayır"))
    res["flags"][k] = {"S1": n1, "S2": n2, "GECERSIZ": n1 >= 2}

lines += ["", "## Coin başına net (2020 evreni)", ""]
for key, r in res["rows"].items():
    lines.append("* %s: %s" % (key, ", ".join("%s %+.1f" % (s.split("/")[0], v) for s, v in r["by_symbol"].items())))

lines += ["", "## Karar (PROTOCOL_V14 §3, mekanik)", "", "| kural | S1 pencere | S2 pencere | hüküm |", "|---|---|---|---|"]
for k, (name, _) in BASE.items():
    f = res["flags"].get(k, {"S1": 0, "S2": 0, "GECERSIZ": False})
    verdict = "**ÖLÇÜLMÜŞ BEKLENTİ GEÇERSİZ (hayatta kalma yanlılığı)**" if f["GECERSIZ"] else (
        "beklenti aşağı revize, ileri test sürer" if (f["S1"] == 1 or f["S2"] >= 2) else "2020 evreninde de ayakta (kanıt değil, güven artışı)")
    lines.append("| %s | %d | %d | %s |" % (name, f["S1"], f["S2"], verdict))
if res["missing"]:
    lines += ["", "**Eksik koşu:** " + ", ".join(res["missing"])]
lines += ["", "VPS'te değişiklik yok. Evren hâlâ 'bugün listede olan' coinlerden oluşuyor (delist edilmiş coin verisi yok)."]
io.open(OUT / "DENEY_V14.md", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
io.open(OUT / "v14_sonuc.json", "w", encoding="utf-8", newline="\n").write(json.dumps(res, ensure_ascii=False, indent=1))
print("DENEY_V14.md yazildi; eksik:", len(res["missing"]), "| bayraklar:", json.dumps(res["flags"]))
