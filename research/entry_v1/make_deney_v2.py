# -*- coding: utf-8 -*-
"""DENEY_V2.md ureticisi — PROTOCOL_V2'deki olcut ve secim kurallarini kosulardan doldurur.

Hicbir sayi elle yazilmaz.
"""
from __future__ import annotations

import io
import json
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

import analyze  # noqa: E402
import benchmarks as bm  # noqa: E402
import entry_rules  # noqa: E402
from report import by_key, full  # noqa: E402

DEV = ("2022-09-01", "2026-09-01")
EVAL = ("2020-11-01", "2022-08-31")
BASE = "dev_base_ungated"

HDR = ("| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def row(label, m):
    if not m or m.get("n", 0) == 0:
        return "| %s | 0 | — | — | — | — | — | — | — | — | — | — | — |" % label
    return "| %s | %d | **%+.2f%%** | %+.4f | %+.4f | %.1f%% | %s | %.2f%% | %s | %.2f | %.1f%% | %d | %d |" % (
        label, m["n"], m["account_return_pct"], m["mean_r"], m["median_r"], 100 * m["win_rate"],
        ("%.3f" % m["profit_factor"]) if m.get("profit_factor") is not None else "-",
        m["max_dd_pct"], m.get("sharpe_daily_realised"), m.get("avg_concurrent_positions") or 0,
        100 * (m.get("exposure_day_share") or 0), m["symbols"], m["months"])


def main():
    out = []
    out.append("# DENEY V2 — giriş kuralı araştırması\n")
    out.append("Protokol sonuçlar görülmeden yazıldı: `PROTOCOL_V2.md`. Bu dosya tamamen koşu")
    out.append("çıktılarından üretildi (`make_deney_v2.py`).\n")

    # ---------------- referanslar
    out.append("## 1. Referanslar — aynı veri, sermaye, maliyet, risk\n")
    out.append(HDR)
    b1a = full("base_gated", "2024-09-01", "2026-09-01")
    b1b_test = full("base_ungated", "2024-09-01", "2026-09-01")
    b1b_dev = full(BASE, *DEV)
    out.append(row("B1a mevcut bot — KAPILI (2024-09→2026-09)", b1a))
    out.append(row("B1b mevcut bot — kapısız (2024-09→2026-09)", b1b_test))
    out.append(row("B1b mevcut bot — kapısız (GELİŞTİRME 2022-09→2026-09)", b1b_dev))
    out.append("| B2 nakit | 0 | **0,00%** | — | — | — | — | 0,00% | — | 0,00 | 0,0% | 0 | 0 |")
    bh_dev = bm.buy_and_hold(*DEV)
    bh_test = bm.buy_and_hold("2024-09-01", "2026-09-01")
    bh_eval = bm.buy_and_hold(*EVAL)
    out.append("| B3 al-tut (GELİŞTİRME) | — | **%+.2f%%** | — | — | — | — | — | — | — | 100%% | 10 | — |" % bh_dev["getiri_pct"])
    out.append("| B3 al-tut (2024-09→2026-09) | — | **%+.2f%%** | — | — | — | — | — | — | — | 100%% | 10 | — |" % bh_test["getiri_pct"])
    out.append("")
    out.append("B3 SPOT benzeri, kaldıraçsız, stop'suz farklı bir maruziyet sınıfıdır ve tek başına")
    out.append("üstünlük iddiası kurmaz. On coin 2026'da seçildiği için **hayatta kalma yanlılığı**")
    out.append("bu satırı yukarı çeker.\n")
    out.append("Sharpe günlük GERÇEKLEŞMİŞ özkaynak serisinden, işlemsiz günler DAHİL hesaplandı.")
    out.append("Gerçekleşmemiş kâr/zarar serilmediği için seri gerçekte olduğundan düzdür ve Sharpe")
    out.append("YUKARI yanlıdır.\n")

    # ---------------- izgara
    out.append("## 2. Geliştirme ızgarası (2022-09-01 → 2026-09-01), 18 yapılandırma\n")
    fams = {}
    for fam, items in entry_rules.GRID.items():
        out.append("### %s\n" % fam)
        out.append(HDR)
        out.append(row("temel (kural yok)", b1b_dev))
        rows = []
        for nm, _fn, _kw in items:
            rid = "dev_" + nm.replace(".", "_")
            try:
                m = full(rid, *DEV)
            except Exception:
                m = None
            rows.append((nm, rid, m))
            out.append(row(nm, m))
        fams[fam] = rows
        out.append("")

    # ---------------- secim
    out.append("## 3. Seçim kuralı (PROTOCOL_V2 sec. 4, önceden yazılı)\n")
    out.append("| aile | en iyi | getiri | n | coin | ay | şart 2 (n≥60, coin≥6, ay≥12) | şart 3 (6 komşunun ≥4'ü temelden iyi) |")
    out.append("|---|---|---|---|---|---|---|---|")
    best_overall = None
    for fam, rows in fams.items():
        ok_rows = [(nm, rid, m) for nm, rid, m in rows if m and m.get("n", 0) > 0]
        if not ok_rows:
            out.append("| %s | — | — | — | — | — | KALDI | KALDI |" % fam)
            continue
        nm, rid, m = max(ok_rows, key=lambda x: x[2]["account_return_pct"])
        c2 = m["n"] >= 60 and m["symbols"] >= 6 and m["months"] >= 12
        better = sum(1 for _n, _r, mm in ok_rows if mm and mm["account_return_pct"] > b1b_dev["account_return_pct"])
        c3 = better >= 4
        out.append("| %s | `%s` | %+.2f%% | %d | %d | %d | %s | %s (%d/6) |" % (
            fam, nm, m["account_return_pct"], m["n"], m["symbols"], m["months"],
            "GEÇTİ" if c2 else "KALDI", "GEÇTİ" if c3 else "KALDI", better))
        if c2 and c3 and (best_overall is None or m["account_return_pct"] > best_overall[2]["account_return_pct"]):
            best_overall = (nm, rid, m)
    out.append("")

    if best_overall is None:
        out.append("**Hiçbir aile şartları sağlamadı → üçü de REDDEDİLDİ, değerlendirme penceresi TÜKETİLMEDİ.**\n")
    else:
        nm, rid, m = best_overall
        out.append("**Seçilen aday: `%s`** (geliştirme getirisi %+.2f%%, n=%d).\n" % (nm, m["account_return_pct"], m["n"]))
        a, _ = analyze.load(rid)
        bb, _ = analyze.load(BASE)
        d = analyze.observed_diff(a, bb)
        pi = analyze.boot_diff(a, bb, paired=True)
        up = analyze.boot_diff(a, bb, paired=False)
        out.append("Temelle fark (işlem başına net R): gözlenen **%+.4f**, eşleştirilmiş %%95 [%+.4f, %+.4f], eşleştirilmemiş %%95 [%+.4f, %+.4f].\n"
                   % (d, pi[1], pi[2], up[1], up[2]))
        json.dump({"selected": nm, "run_id": rid, "dev_return_pct": m["account_return_pct"], "n": m["n"]},
                  io.open("out/selected_rule.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------------- degerlendirme penceresi
    out.append("## 4. Değerlendirme penceresi (2020-11-01 → 2022-08-31) — hiç kullanılmamış\n")
    ev = None
    if best_overall:
        try:
            ev = full("eval_" + best_overall[0].replace(".", "_"), *EVAL)
        except Exception:
            ev = None
    evb = None
    try:
        evb = full("eval_base_ungated", *EVAL)
    except Exception:
        pass
    out.append(HDR)
    out.append(row("temel — kapısız", evb))
    if best_overall:
        out.append(row("aday `%s`" % best_overall[0], ev))
    out.append("| B3 al-tut | — | **%+.2f%%** | — | — | — | — | — | — | — | 100%% | 10 | — |" % bh_eval["getiri_pct"])
    out.append("")
    out.append("Bu pencere geliştirmeden ÖNCEdir, ileri dönem DEĞİLDİR. 2021 yükselişi ve 2022")
    out.append("düşüşünü kapsar; al-tut referansı bu yüzden çok yüksektir ve seçim yanlılığı burada")
    out.append("en büyüktür.\n")

    # ---------------- dagilim
    if best_overall:
        out.append("## 5. Adayın dağılımı (geliştirme)\n")
        for k, title in (("symbol", "coin"), ("regime", "rejim"), ("side", "yön"), ("exit", "çıkış")):
            dd = by_key(best_overall[1], k)
            out.append("**%s:** %s\n" % (title, ", ".join("%s n=%d net=%+.1f" % (kk, v["n"], v["net"]) for kk, v in list(dd.items())[:8])))

    io.open("out/DENEY_V2.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
