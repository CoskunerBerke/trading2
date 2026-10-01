# -*- coding: utf-8 -*-
"""SLOT SAYISI DENEYI — kuyruk yakalamak icin kac bilet gerekiyor? (2026-09-20)

OLCULEN SORUN
-------------
Kar her kolda EN IYI 3 ISLEME yogun; uc kolda da en iyi 3 islem NET KARIN %100'unden
fazlasi (M2 P1 10coin %161, P3 10coin %113, P3 40coin %103). Geri kalan her sey toplamda
eksi. Yani kural bir KUYRUK YAKALAMA mekanizmasi.

P1'de 10 coinlik kolun tum kari tek bir SOL islemi (+23,2). 40 coinlik kollarda o islem
HIC alinmadi (en iyi islem +3,7) ve SOL +19,18 yerine -6,31/-8,47/-7,96 verdi. Siralama
bunu KURTARMADI. Cunku sorun "hangi sirayla" degil: 3 slotla 40 coin kovalamak,
firsatlarin ~%7,5'ini ornekleyip kuyrugu sansa birakmaktir.

DENEY
-----
Eszamanli pozisyon sayisi = `max_total_open_risk_pct / risk_per_trade_pct`.
TOPLAM riski SABIT (%6) tutup islem basina riski dusurmek slot sayisini cogaltir:
  risk %2.0 -> 3 slot   (BUGUNKU CANLI AYAR)
  risk %1.0 -> 6 slot
  risk %0.5 -> 12 slot
Uc kolda da portfoy maruziyeti AYNI. Bu bir risk gevsetmesi DEGIL, cesitlendirmedir —
ve tam da bu yuzden kullaniciya sorulmadan olculebilir: toplam risk tavani degismiyor.

Iki evren (10 ve 40) x iki yeni slot ayari (6, 12) x iki kural x uc pencere = 24 kosu.
3 slotluk hucreler DAHA ONCE olculdu (grid.json / rank40.json) ve burada tekrar kosulmaz.
Secim her kolda C (siralanmis) — izgarada olculen daha iyi secim.
"""
from __future__ import annotations

import io
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
sys.path.insert(0, str(ROOT))

ARCHIVE = r"C:/Users/berke/research/bn_archive"
OUT = Path(__file__).resolve().parent / "slots.json"
WINDOWS = [("P3", "2024-09-01", "2026-08-31"),
           ("P2", "2022-09-01", "2024-08-31"),
           ("P1", "2020-11-01", "2022-08-31")]
TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
       "DOGE/USDT", "LINK/USDT", "AAVE/USDT", "AVAX/USDT", "LTC/USDT"]
RISKS = [(1.0, "6slot"), (0.5, "12slot")]          # 3 slot zaten olculdu
EVRENLER = [("40c", None), ("10c", TEN)]

results = []
if OUT.exists():
    try:
        results = [r for r in json.load(io.open(OUT, encoding="utf-8")) if isinstance(r, dict)]
        print("surduruluyor: %d kayit" % len(results), flush=True)
    except Exception:
        results = []
DONE = {r["run_id"] for r in results if r.get("ok")}


def _emit():
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=1, default=str))


def _guc(sym, act):
    try:
        c, atr = act.get("signal_close"), act.get("atr14")
        esik = act.get("ref_close") if act.get("ref_close") is not None else act.get("ema200")
        if c is None or atr is None or esik is None or float(atr) <= 0:
            return None
        return (float(c) - float(esik)) / float(atr)
    except Exception:
        return None


def _funding(fc):
    if not isinstance(fc, dict):
        return "KAPSAM_YOK"
    q, a = int(fc.get("queries") or 0), int(fc.get("answered") or 0)
    if q == 0:
        return "SORULMADI"
    return "TAM(%d/%d)" % (a, q) if a == q else "EKSIK(%d/%d)" % (a, q)


def _yogunluk(tr):
    """En iyi 3 islemin net kara orani — kuyruk bagimliligi olcusu."""
    if not tr:
        return None
    nets = sorted((t["net"] for t in tr), reverse=True)
    net = sum(nets)
    return round(100.0 * sum(nets[:3]) / net, 1) if net > 0 else None


def main():
    import analyze
    import momentum_rules
    import run_rule
    import strategy_rules

    for wname, dfrom, dto in WINDOWS:
        for rule in ("m2_tsmom28", "t2_trend_regime"):
            for ev, syms in EVRENLER:
                for risk, slot_ad in RISKS:
                    rid = "sl_%s_%s_%s_%s" % (rule.split("_")[0], wname.lower(), ev, slot_ad)
                    if rid in DONE:
                        print("%-3s %-4s %-4s %-7s -> ATLANDI" % (wname, rule.split("_")[0], ev, slot_ad), flush=True)
                        continue
                    row = {"pencere": wname, "rule": rule, "evren": ev, "slot": slot_ad,
                           "risk_pct": risk, "run_id": rid}
                    t0 = time.time()
                    try:
                        try:
                            s = strategy_rules.build(rule)
                        except KeyError:
                            s = momentum_rules.build(rule)
                        meta = run_rule.run(rid, None, dfrom, dto, strategy=s, no_breakeven=True,
                                            symbols=syms, cache_dir=ARCHIVE,
                                            require_all=(syms is None), candidate_order=_guc,
                                            risk_pct=risk)
                        tr, led = analyze.load(rid)
                        m = analyze.metrics(tr, led)
                        row.update({"ok": True, "funding": _funding(meta.get("funding_coverage")),
                                    "n_opened": meta.get("n_opened"), "n_kapanan": m.get("n"),
                                    "islem_goren_coin": m.get("symbols"),
                                    "getiri_pct": round(float(m.get("final_equity", 100.0)) - 100.0, 2),
                                    "ort_R": m.get("mean_r"), "isabet": m.get("win_rate"),
                                    "maks_dd": m.get("max_drawdown_usdt"), "pf": m.get("profit_factor"),
                                    "en_iyi3_oran": _yogunluk(tr)})
                    except Exception as exc:
                        row.update({"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc),
                                    "iz": traceback.format_exc()[-700:]})
                    row["elapsed_s"] = round(time.time() - t0, 1)
                    results.append(row)
                    _emit()
                    print("%-3s %-4s %-4s %-7s -> %s" % (wname, rule.split("_")[0], ev, slot_ad,
                          ("getiri=%+8.1f%% n=%-4s coin=%-3s ortR=%-7s dd=%-7s en_iyi3=%s%% %s" % (
                              row.get("getiri_pct") or 0.0, row.get("n_kapanan"), row.get("islem_goren_coin"),
                              row.get("ort_R"), row.get("maks_dd"), row.get("en_iyi3_oran"), row.get("funding")))
                          if row.get("ok") else "HATA " + str(row.get("hata"))[:120]), flush=True)


if __name__ == "__main__":
    main()
    print("=== BITTI ===")
