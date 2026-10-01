# -*- coding: utf-8 -*-
"""KALDIRAC VE BAKIYE — iki AYRI kisiti ayri ayri olcer (2026-09-20).

KULLANICI TALIMATI: "kaldiracli islem girdigimiz icin 1x sakin olmasin" ve "gerekirse
bakiyeyi 200 dolar yapalim". Ikisi de yapilabilir; hangisinin ne yaptigi OLCULUR.

IKI KISIT AYRI SEYLERI COZER — karistirmamak icin ayri boyut:
  * KALDIRAC  -> `MAX_POSITION_PCT x kaldirac` tavanini acar. 1x'te tavan 30 USDT'dir ve
    stop mesafesi ozkaynagin %6,67'sinden DAR olan her sinyal bu tavana takilip SESSIZCE
    reddedilir (box'ta olculup leverage:3 ile cozulmustu; trendde cozulmemisti).
    Kaldirac islem basina RISKI ARTIRMAZ — risk stop mesafesiyle belirlenir ve
    `risk_per_trade_pct` degismez. Artirdigi sey likidasyon YAKINLIGIDIR.
  * BAKIYE    -> borsa MINIMUMLARI (min notional 5 USDT, lot adimi) mutlak USDT cinsindendir.
    Olculdu: slot sayisi artinca pozisyon kuculuyor ve MIN_ORDER_CONFLICT patliyordu
    (12 slotta 14906 red, acilan 36 -> 17). Bakiye bunu gevsetir, kaldirac gevsetMEZ.

TASARIM: 40 coin, siralanmis secim (C), 3 slot (boyutlandirilabilen tek ayar).
  (kaldirac, bakiye) hucreleri: (2,100) (3,100) (5,100) (1,200) (3,200)
  (1,100) zaten olculdu -> rank40.json C kolu. Boylece HER IKI eksen de tek basina izlenir.
2 kural x 3 pencere = hucre basina 6 kosu.

SAYILAN SEY: getiri yaninda MAX_POSITION_PCT / MIN_NOTIONAL / STEP_ZERO_QTY redleri de
raporlanir — kaldirac dogru kisiti gevsetiyor mu, sayiyla gorunur.
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
OUT = Path(__file__).resolve().parent / "lev.json"
WINDOWS = [("P3", "2024-09-01", "2026-08-31"),
           ("P2", "2022-09-01", "2024-08-31"),
           ("P1", "2020-11-01", "2022-08-31")]
CELLS = [(2, 100.0), (3, 100.0), (5, 100.0), (1, 200.0), (3, 200.0)]

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
    return "TAM" if a == q else "EKSIK(%d/%d)" % (a, q)


BOYUT_REDLERI = ("MAX_POSITION_PCT", "MIN_NOTIONAL", "STEP_ZERO_QTY", "MIN_ORDER_CONFLICT")


def main():
    import analyze
    import momentum_rules
    import run_rule
    import strategy_rules

    for wname, dfrom, dto in WINDOWS:
        for rule in ("m2_tsmom28", "t2_trend_regime"):
            for lev, eq in CELLS:
                rid = "lv_%s_%s_l%d_e%d" % (rule.split("_")[0], wname.lower(), lev, int(eq))
                if rid in DONE:
                    print("%-3s %-4s lev%d/%dUSDT -> ATLANDI" % (wname, rule.split("_")[0], lev, eq), flush=True)
                    continue
                row = {"pencere": wname, "rule": rule, "kaldirac": lev, "bakiye": eq, "run_id": rid}
                t0 = time.time()
                try:
                    try:
                        s = strategy_rules.build(rule, leverage=lev)
                    except (KeyError, TypeError):
                        s = momentum_rules.build(rule)     # M2 momentum_rules'ta ise kaldirac gecmez
                    meta = run_rule.run(rid, None, dfrom, dto, strategy=s, no_breakeven=True,
                                        symbols=None, cache_dir=ARCHIVE, require_all=True,
                                        candidate_order=_guc, equity=eq)
                    tr, led = analyze.load(rid)
                    m = analyze.metrics(tr, led)
                    rj = ((meta.get("rejections") or {}).get("by_reason") or {})
                    row.update({"ok": True, "funding": _funding(meta.get("funding_coverage")),
                                "n_opened": meta.get("n_opened"), "n_kapanan": m.get("n"),
                                "islem_goren_coin": m.get("symbols"),
                                # getiri BAKIYEDEN bagimsiz olsun diye YUZDE olarak
                                "getiri_pct": round(100.0 * (float(m.get("net_usdt", 0.0)) / eq), 2),
                                "net_usdt": m.get("net_usdt"),
                                "ort_R": m.get("mean_r"), "isabet": m.get("win_rate"),
                                "maks_dd": m.get("max_drawdown_usdt"), "pf": m.get("profit_factor"),
                                "boyut_redleri": {k: int(rj.get(k, 0)) for k in BOYUT_REDLERI}})
                except Exception as exc:
                    row.update({"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc),
                                "iz": traceback.format_exc()[-700:]})
                row["elapsed_s"] = round(time.time() - t0, 1)
                results.append(row)
                _emit()
                print("%-3s %-4s lev%d/%-4dUSDT -> %s" % (wname, rule.split("_")[0], lev, eq,
                      ("getiri=%+7.1f%% n=%-4s ortR=%-7s dd=%-7s boyut_red=%s %s" % (
                          row.get("getiri_pct") or 0.0, row.get("n_kapanan"), row.get("ort_R"),
                          row.get("maks_dd"), row.get("boyut_redleri"), row.get("funding")))
                      if row.get("ok") else "HATA " + str(row.get("hata"))[:110]), flush=True)


if __name__ == "__main__":
    main()
    print("=== BITTI ===")
