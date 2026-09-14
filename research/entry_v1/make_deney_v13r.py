# -*- coding: utf-8 -*-
"""DENEY_V13R.md — PROTOCOL_V13R bayraklarini (F1..F4) onceden yazili kurallarla uygular. Yorum eklemez."""
import io
import json
import math
import statistics as st
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
WINS = ("p1", "p2", "p3")
WIN_TXT = {"p1": "P1 2020-11→2022-08", "p2": "P2 2022-09→2024-08", "p3": "P3 2024-09→2026-08"}
SHIFT_TXT = {"p1": "2021-02→2022-11", "p2": "2022-12→2024-11", "p3": "2024-12→2026-08"}
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "DOGE", "AVAX", "LTC", "AAVE"]
BASE = {"t2": ("T2 EMA200", "v9_t2_%s"), "m2": ("M2 TSMOM 28g", "v11_m2_%s")}


def ret(rid):
    """Hesap getirisi % (baslangic 100) ya da None (kosu yok/bos)."""
    try:
        tr, _ = A.load_full(rid)
    except FileNotFoundError:
        return None
    return round(sum(t["net"] for t in tr), 2)


def trades(rid):
    try:
        return A.load_full(rid)[0]
    except FileNotFoundError:
        return []


def pct(v):
    return "—" if v is None else ("%+.1f%%" % v)


def flag(v, base):
    return v is not None and base is not None and base > 0 and v < 0


lines = ["# DENEY V13R — T2 ve M2'yi kırma denemesi (PROTOCOL_V13R bayrakları)", "",
         "Kod `3501304`; yalnız araştırma paketi. Getiriler maliyet sonrası hesap getirisi (başlangıç 100 USDT),",
         "üretim defteri kuralları (2% risk, %6 tavan, gerçek borsa filtreleri, funding). Tabanlar: `v9_t2_*`, `v11_m2_*`.", ""]
result = {"flags": {}, "loo": {}, "neighbors": {}, "shift": {}, "concentration": {}, "missing": []}

# ---------------------------------------------------------------- R1 LOO
lines += ["## R1 — coin çıkarma (LOO): tek coin yeni giriş açamaz", ""]
for k, (name, base_fmt) in BASE.items():
    lines += ["### %s" % name, "", "| pencere | taban | " + " | ".join("−" + c for c in COINS) + " | en düşük | F1 |", "|---|---|" + "---|" * len(COINS) + "---|---|"]
    fl = {}
    for p in WINS:
        base = ret(base_fmt % p)
        vals = {c: ret("v13r_loo_%s_%s_%s" % (k, p, c)) for c in COINS}
        for c, v in vals.items():
            if v is None:
                result["missing"].append("v13r_loo_%s_%s_%s" % (k, p, c))
        have = {c: v for c, v in vals.items() if v is not None}
        mn = min(have.values()) if have else None
        arg = min(have, key=have.get) if have else "—"
        f1 = flag(mn, base)
        conc = (mn is not None and base is not None and base > 0 and mn < base / 2.0)
        fl[p] = {"base": base, "min": mn, "argmin": arg, "F1": f1, "yogun": conc, "n": len(have)}
        lines.append("| %s | %s | %s | %s (−%s) | %s |" % (WIN_TXT[p], pct(base), " | ".join(pct(vals[c]) for c in COINS), pct(mn), arg,
                                                         ("**EVET**" if f1 else ("hayır" + (" · yoğun" if conc else "")))))
    result["loo"][k] = fl
    lines.append("")

# ---------------------------------------------------------------- R2 komsu
lines += ["## R2 — komşu parametreler", "", "| pencere | M2 28g (taban) | M2 21g | M2 42g | F2 | T2 üretim | T2 kontrol (araştırma EMA200) | EMA150 | EMA250 | F2 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
nb = {}
for p in WINS:
    m_base = ret("v11_m2_%s" % p)
    m21, m42 = ret("v13r_m2_tsmom21_%s" % p), ret("v13r_m2_tsmom42_%s" % p)
    t_prod, t_ctl = ret("v9_t2_%s" % p), ret("v13r_t2r_ema200_%s" % p)
    e150, e250 = ret("v13r_t2r_ema150_%s" % p), ret("v13r_t2r_ema250_%s" % p)
    f2m = flag(m21, m_base) or flag(m42, m_base)
    f2t = flag(e150, t_ctl) or flag(e250, t_ctl)
    for rid, v in (("v13r_m2_tsmom21_%s" % p, m21), ("v13r_m2_tsmom42_%s" % p, m42), ("v13r_t2r_ema200_%s" % p, t_ctl),
                   ("v13r_t2r_ema150_%s" % p, e150), ("v13r_t2r_ema250_%s" % p, e250)):
        if v is None:
            result["missing"].append(rid)
    nb[p] = {"m2_base": m_base, "m2_21": m21, "m2_42": m42, "F2_m2": f2m, "t2_prod": t_prod, "t2_ctl": t_ctl,
             "ema150": e150, "ema250": e250, "F2_t2": f2t}
    lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        WIN_TXT[p], pct(m_base), pct(m21), pct(m42), "**EVET**" if f2m else "hayır",
        pct(t_prod), pct(t_ctl), pct(e150), pct(e250), "**EVET**" if f2t else "hayır"))
result["neighbors"] = nb
lines += ["", "Kontrol ile üretim T2 farkı, EMA hesap yönteminden gelir (üretim: gösterge sütunu; araştırma: kapanışlardan). ",
          "T2 komşuları KONTROL ile karşılaştırılır.", ""]

# ---------------------------------------------------------------- R3 kaydirma
lines += ["## R3 — pencere kaydırma (+3 ay)", "", "| pencere | T2 taban | T2 kaydırılmış | F3 | M2 taban | M2 kaydırılmış | F3 |", "|---|---|---|---|---|---|---|"]
sh = {}
for p in WINS:
    tb, ts = ret("v9_t2_%s" % p), ret("v13r_shift_t2_%s" % p)
    mb, ms = ret("v11_m2_%s" % p), ret("v13r_shift_m2_%s" % p)
    for rid, v in (("v13r_shift_t2_%s" % p, ts), ("v13r_shift_m2_%s" % p, ms)):
        if v is None:
            result["missing"].append(rid)
    sh[p] = {"t2_base": tb, "t2_shift": ts, "F3_t2": flag(ts, tb), "m2_base": mb, "m2_shift": ms, "F3_m2": flag(ms, mb)}
    lines.append("| %s → %s | %s | %s | %s | %s | %s | %s |" % (WIN_TXT[p][:2], SHIFT_TXT[p], pct(tb), pct(ts), "**EVET**" if sh[p]["F3_t2"] else "hayır",
                                                           pct(mb), pct(ms), "**EVET**" if sh[p]["F3_m2"] else "hayır"))
result["shift"] = sh
lines.append("")

# ---------------------------------------------------------------- R4 yogunlasma (kosu yok)
lines += ["## R4 — yoğunlaşma (mevcut taban kayıtlarından)", "",
          "| pencere | kural | işlem | en iyi coin (net payı) | en iyi 3 işlem / brüt kâr | F4 |", "|---|---|---|---|---|---|"]
conc = {}
for k, (name, base_fmt) in BASE.items():
    for p in WINS:
        tr = trades(base_fmt % p)
        if not tr:
            continue
        net = sum(t["net"] for t in tr)
        by = {}
        for t in tr:
            by[t["symbol"]] = by.get(t["symbol"], 0.0) + t["net"]
        top_sym = max(by, key=by.get)
        gross = sum(t["net"] for t in tr if t["net"] > 0)
        top3 = sum(sorted((t["net"] for t in tr if t["net"] > 0), reverse=True)[:3])
        share3 = (top3 / gross) if gross > 0 else None
        f4 = share3 is not None and share3 > 0.5
        conc["%s_%s" % (k, p)] = {"n": len(tr), "net": round(net, 2), "top_symbol": top_sym, "top_symbol_net": round(by[top_sym], 2),
                                  "top_symbol_share_of_net": round(by[top_sym] / net, 3) if net else None,
                                  "top3_share_of_gross": round(share3, 3) if share3 is not None else None, "F4": f4,
                                  "by_symbol": {s: round(v, 2) for s, v in sorted(by.items(), key=lambda x: -x[1])}}
        lines.append("| %s | %s | %d | %s (%s USDT, net'in %s) | %s | %s |" % (
            WIN_TXT[p], name, len(tr), top_sym.split("/")[0], "%+.1f" % by[top_sym],
            ("%.0f%%" % (100 * by[top_sym] / net)) if net else "—",
            ("%.0f%%" % (100 * share3)) if share3 is not None else "—", "**EVET**" if f4 else "hayır"))
result["concentration"] = conc

# T2–M2 aylik net korelasyonu (pencere icinde)
lines += ["", "**T2–M2 aylık net korelasyonu (birleşik defter çeşitlendirmesi):**", ""]
corr = {}
for p in WINS:
    a, b = trades("v9_t2_%s" % p), trades("v11_m2_%s" % p)
    months = sorted({t["month"] for t in a} | {t["month"] for t in b})
    if len(months) < 6:
        continue
    sa = [sum(t["net"] for t in a if t["month"] == m) for m in months]
    sb = [sum(t["net"] for t in b if t["month"] == m) for m in months]
    ma, mb = st.mean(sa), st.mean(sb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(sa, sb))
    den = math.sqrt(sum((x - ma) ** 2 for x in sa) * sum((y - mb) ** 2 for y in sb))
    r = cov / den if den else None
    corr[p] = None if r is None else round(r, 3)
    lines.append("* %s: r = %s (%d ay)" % (WIN_TXT[p], "—" if r is None else "%.2f" % r, len(months)))
result["corr_t2_m2_monthly"] = corr

# ---------------------------------------------------------------- karar
lines += ["", "## Karar (PROTOCOL_V13R §2, mekanik)", "", "| kural | F1 pencere | F2 pencere | F3 pencere | hüküm |", "|---|---|---|---|---|"]
for k, (name, _) in BASE.items():
    n1 = sum(1 for p in WINS if result["loo"][k][p]["F1"])
    n2 = sum(1 for p in WINS if nb[p]["F2_%s" % k])
    n3 = sum(1 for p in WINS if sh[p]["F3_%s" % k])
    fragile = max(n1, n2, n3) >= 2
    result["flags"][k] = {"F1": n1, "F2": n2, "F3": n3, "KIRILGAN": fragile}
    lines.append("| %s | %d | %d | %d | %s |" % (name, n1, n2, n3, "**KIRILGAN**" if fragile else "kırılamadı (kanıt değil, güven artışı)"))
if result["missing"]:
    lines += ["", "**Eksik koşu:** %d — %s" % (len(result["missing"]), ", ".join(result["missing"][:12]) + (" …" if len(result["missing"]) > 12 else ""))]
lines += ["", "Hayatta kalma yanlılığı bu turda ölçülmedi (V14, VPS verisi). VPS'te değişiklik yok."]

io.open(OUT / "DENEY_V13R.md", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
io.open(OUT / "v13r_sonuc.json", "w", encoding="utf-8", newline="\n").write(json.dumps(result, ensure_ascii=False, indent=1))
print("DENEY_V13R.md yazildi; eksik kosu:", len(result["missing"]), "| bayraklar:", json.dumps(result["flags"]))
