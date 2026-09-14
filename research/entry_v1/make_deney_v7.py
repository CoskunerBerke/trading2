# -*- coding: utf-8 -*-
"""PROTOCOL_V7 Q1 sonuç üreticisi (3 pencere, 5 şart + rejim istisnası + toplam ≥80 işlem).
Sonuç görülmeden yazıldı. Q2 (benchmarks_v7.json) ve Q3 (v7_q3_*.json varsa) tablolara eklenir."""
import datetime as _dt
import glob
import gzip
import io
import json
import statistics as st
from pathlib import Path

import analyze as A

ROOT = Path(r"C:/Users/berke/research/entry_v1")
OUT = ROOT / "out"
HIST = r"C:/Users/berke/wt-ten/data/history/futures"
ARMS = [("b0", "B0 temel (üretim)"), ("r1", "R1 yalnız LONG + yalnız UP"),
        ("r2", "R2 DOWN'da giriş yok"), ("r3", "R3 rejimi izle")]
WINS = [("p1", "2020-11-01", "2022-08-31"), ("p2", "2022-09-01", "2024-08-31"), ("p3", "2024-09-01", "2026-08-31")]
START = 100.0
UP_SHARE_MIN = 0.30
TOTAL_MIN_TRADES = 80


def rid_of(arm, win):
    if arm == "b0":
        return "v6_b0_p3" if win == "p3" else "v4_c3_%s" % win
    return "v7_%s_%s" % (arm, win)


def _ms(s):
    return int(_dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def fmt_pct(x): return ("%+.2f%%" % x).replace(".", ",")
def fmt_r(x): return "—" if x is None else ("%+.4f" % x).replace(".", ",")
def fmt_ci(t): return "—" if (not t or t[1] is None) else "[%s, %s]" % (fmt_r(t[1]), fmt_r(t[2]))


def btc_up_share(f, t):
    rows = []
    for p in sorted(glob.glob("%s/BTC_USDT/1d/*/*.csv.gz" % HIST)):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            head = fh.readline().strip().split(","); i_ts, i_c = head.index("timestamp"), head.index("close")
            for line in fh:
                parts = line.rstrip("\n").split(",")
                try:
                    rows.append((int(float(parts[i_ts])), float(parts[i_c])))
                except (ValueError, IndexError):
                    continue
    rows.sort()
    cl = [r[1] for r in rows]
    k, e, ema = 2.0 / 201, None, []
    for i, c in enumerate(cl):
        if i < 199: ema.append(None)
        elif i == 199: e = sum(cl[:200]) / 200; ema.append(e)
        else: e = c * k + e * (1 - k); ema.append(e)
    t0, t1 = _ms(f), _ms(t)
    idx = [i for i, r in enumerate(rows) if t0 <= r[0] <= t1]
    up = sum(1 for i in idx if ema[i] is not None and cl[i] > ema[i])
    return (up / len(idx)) if idx else None


def arm_row(rid):
    meta = json.load(io.open(OUT / ("meta_%s.json" % rid), encoding="utf-8"))
    tr, _ = A.load_full(rid)
    m = A.metrics(tr)
    net = m.get("net_usdt", 0.0) if tr else 0.0
    return {"run_id": rid, "n": len(tr), "net_pct": net / START * 100.0,
            "stress_pct": (sum(t["net"] - t["fees"] - t["slip"] for t in tr) / START * 100.0) if tr else 0.0,
            "mean_r": m.get("mean_r"), "median_r": m.get("median_r"), "win_rate": m.get("win_rate"),
            "pf": m.get("profit_factor"), "max_dd_pct": (m.get("max_drawdown_usdt") or 0.0) / START * 100.0,
            "symbols": m.get("symbols", 0), "n_long": sum(1 for t in tr if t["side"] == "LONG"),
            "n_decisions": meta.get("n_decisions"), "n_actionable": meta.get("n_actionable"),
            "n_opened": meta.get("n_opened"), "hash": meta.get("determinism_hash"),
            "rejections": (meta.get("rejections") or {}).get("by_reason", {}), "trades": tr}


def kept_removed(base_tr, arm_tr):
    key = lambda t: (t["symbol"], t["opened_at"])
    bk, ak = {key(t): t for t in base_tr}, {key(t): t for t in arm_tr}
    kept = [bk[k] for k in bk if k in ak]; rem = [bk[k] for k in bk if k not in ak]; new = [ak[k] for k in ak if k not in bk]
    mr = lambda xs: (st.mean(t["r"] for t in xs) if xs else None)
    return {"kept_n": len(kept), "kept_r": mr(kept), "removed_n": len(rem), "removed_r": mr(rem), "new_n": len(new), "new_r": mr(new)}


def criteria(row, base, kr, up_share):
    c1 = row["net_pct"] > 0 or (row["n"] == 0 and up_share is not None and up_share < UP_SHARE_MIN)
    c2 = row["net_pct"] > base["net_pct"]
    n_applicable = not (up_share is not None and up_share < UP_SHARE_MIN)
    c3 = (row["n"] >= 40 and row["symbols"] >= 5) if n_applicable else True
    c4 = row["stress_pct"] > 0 or (row["n"] == 0 and not n_applicable)
    c5 = (kr["kept_r"] is not None and kr["removed_r"] is not None and kr["kept_r"] >= kr["removed_r"]) \
        or (kr["removed_n"] == 0 and kr["kept_n"] > 0) or (row["n"] == 0 and not n_applicable)
    return {"1_pozitif": c1, "2_temelden_iyi": c2, "3_n40_coin5": c3, "3_uygulanabilir": n_applicable,
            "4_stres_pozitif": c4, "5_tutulan_ge_elenen": c5, "hepsi": c1 and c2 and c3 and c4 and c5}


def main():
    up_share = {p: btc_up_share(f, t) for p, f, t in WINS}
    rows = {(a, p): arm_row(rid_of(a, p)) for a, _ in ARMS for p, _, _ in WINS}
    verdict, ci, kr_all = {}, {}, {}
    for a, _ in ARMS[1:]:
        per = {}
        for p, _, _ in WINS:
            kr = kept_removed(rows[("b0", p)]["trades"], rows[(a, p)]["trades"]); kr_all[(a, p)] = kr
            per[p] = criteria(rows[(a, p)], rows[("b0", p)], kr, up_share[p])
            ci[(a, p)] = {"observed": A.observed_diff(rows[(a, p)]["trades"], rows[("b0", p)]["trades"]),
                          "boot": A.boot_diff(rows[(a, p)]["trades"], rows[("b0", p)]["trades"], paired=True)}
        total = sum(rows[(a, p)]["n"] for p, _, _ in WINS)
        allw = all(per[p]["hepsi"] for p, _, _ in WINS) and total >= TOTAL_MIN_TRADES
        anyw = any(per[p]["hepsi"] for p, _, _ in WINS)
        verdict[a] = {"per_window": per, "total_trades": total,
                      "karar": ("GÖLGE PAPER ADAYI" if allw else
                                "yalnız bazı pencerelerde olumlu, DOĞRULANMADI" if anyw else "REDDEDİLDİ")}
    js = {"up_share": up_share, "rows": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in rows.items()},
          "diff_vs_b0": {"%s_%s" % k: v for k, v in ci.items()}, "kept_removed": {"%s_%s" % k: v for k, v in kr_all.items()},
          "verdict": verdict}
    io.open(OUT / "v7_sonuc.json", "w", encoding="utf-8").write(json.dumps(js, ensure_ascii=False, indent=1, default=str))

    L = ["# DENEY V7 — piyasa rejimi kapısı, basit temel, hayatta kalma kontrolü", "",
         "Protokol: `PROTOCOL_V7.md` (sonuçlar görülmeden yazıldı). Temel B0 = üretim (tetik + C3 mum vetosu).", "",
         "BTC UP rejimi gün payı (close > EMA200): " + ", ".join("%s %s" % (p.upper(), ("%.0f%%" % (100 * up_share[p])) if up_share[p] is not None else "—") for p, _, _ in WINS), "",
         "## 1. Q1 sonuçları", "",
         "| pencere | kol | işlem | LONG | hesap getirisi | stresli | ort net R | isabet | PF | maks düşüş | coin |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p, f, t in WINS:
        for a, name in ARMS:
            r = rows[(a, p)]
            L.append("| %s %s→%s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %d |" % (
                p.upper(), f[:7], t[:7], name, r["n"], r["n_long"], fmt_pct(r["net_pct"]), fmt_pct(r["stress_pct"]), fmt_r(r["mean_r"]),
                ("%%%.1f" % (100 * r["win_rate"])).replace(".", ",") if r["win_rate"] is not None else "—",
                ("%.3f" % r["pf"]).replace(".", ",") if r["pf"] else "—", ("%.2f%%" % r["max_dd_pct"]).replace(".", ","), r["symbols"]))
    L += ["", "## 2. Kabul ölçütleri (5 şart; rejim istisnası; üç pencere + toplam ≥80 işlem)", "",
          "| kol | P1 | P2 | P3 | toplam işlem | karar |", "|---|---|---|---|---|---|"]
    keys = ("1_pozitif", "2_temelden_iyi", "3_n40_coin5", "4_stres_pozitif", "5_tutulan_ge_elenen")
    for a, name in ARMS[1:]:
        v = verdict[a]
        cell = lambda p: " ".join(("✓" if v["per_window"][p][k] else "✗") + ("°" if k == "3_n40_coin5" and not v["per_window"][p]["3_uygulanabilir"] else "") for k in keys)
        L.append("| %s | %s | %s | %s | %d | **%s** |" % (name, cell("p1"), cell("p2"), cell("p3"), v["total_trades"], v["karar"]))
    L += ["", "° = pencerede UP payı < %30, şart 3 uygulanamaz sayıldı.", "",
          "## 3. Tutulan / elenen / yeni işlemler", "",
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
    L += ["", "## 5. Ret dökümü", "", "| koşu | uygulanabilir | açılan | kural retleri |", "|---|---|---|---|"]
    for p, _, _ in WINS:
        for a, _ in ARMS[1:]:
            r = rows[(a, p)]
            rr = {k: v for k, v in r["rejections"].items() if k[:3] in ("R1_", "R2_", "R3_")}
            L.append("| %s | %s | %s | %s |" % (r["run_id"], r["n_actionable"], r["n_opened"], ", ".join("%s=%s" % kv for kv in sorted(rr.items(), key=lambda kv: -kv[1])) or "—"))
    bp = OUT / "benchmarks_v7.json"
    if bp.exists():
        b = json.load(io.open(bp, encoding="utf-8"))
        L += ["", "## 6. Q2 — referanslar (kaldıraçsız, stopsuz; farklı maruziyet sınıfı)", "",
              "| pencere | bot (B0) | al-tut | EMA200 trend | trend pozisyonda gün payı | trend giriş/çıkış |", "|---|---|---|---|---|---|"]
        for p, _, _ in WINS:
            q = b.get(p.upper(), {})
            L.append("| %s | %s | %s | %s | %s | %s |" % (p.upper(), fmt_pct(rows[("b0", p)]["net_pct"]), fmt_pct(q.get("al_tut_pct", 0)),
                                                        fmt_pct(q.get("ema200_trend_pct", 0)), q.get("trend_pozisyonda_gun_payi"), q.get("trend_switches")))
    L += ["", "## 7. Yorum", "", "_(sonuçlar görüldükten sonra elle yazılır)_", ""]
    io.open(OUT / "DENEY_V7.md", "w", encoding="utf-8").write("\n".join(L))
    print(json.dumps({"up_share": up_share, "verdict": {a: v["karar"] for a, v in verdict.items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
