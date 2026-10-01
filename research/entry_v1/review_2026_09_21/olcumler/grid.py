# -*- coding: utf-8 -*-
"""IZGARA: {10 coin, 40 coin} x {A sirasiz, B iki-fazli, C siralanmis} (2026-09-20).

NEDEN IZGARA: Tek hucre eklemek "sirala mi, evreni kis mi" sorusunu cevaplamaz — iki
degisken ETKILESEBILIR. Siralamanin kitlik olmadan (10 coin, 3 slot, cogu zaman aday < slot)
etkisiz kalmasi beklenir; olculmeden varsayilamaz.

KOLLAR (hepsi ayni arsiv/pencere/tohum/koruma; no_breakeven=True):
  A : candidate_order=None      -> tek gecis, arsiv sirasi (CANLI DAVRANIS)
  B : iki fazli, sira KORUNUR   -> "cikislari once uygulamak"in tek basina etkisi
  C : iki fazli, siralanmis     -> anahtar (kapanis - cikis esigi) / ATR14

TAMAMLANMIS HUCRELER YENIDEN KOSULMAZ: 40A/40B/40C ve 10A daha once olculdu
(run40.json, rank40.json). Bu betik yalniz 10B ve 10C'yi kosar ve hepsini TEK tabloda
birlestirir.

ONEMLI: 10 coinlik kol da AYNI arsivden (bn_archive) okunur — fark "evren" olsun,
"veri kaynagi" DEGIL.
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
SC = Path(__file__).resolve().parent
OUT = SC / "grid.json"
WINDOWS = [("P3", "2024-09-01", "2026-08-31"),
           ("P2", "2022-09-01", "2024-08-31"),
           ("P1", "2020-11-01", "2022-08-31")]
TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
       "DOGE/USDT", "LINK/USDT", "AAVE/USDT", "AVAX/USDT", "LTC/USDT"]

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
    """Sinyalin cikis esigine ATR cinsinden uzakligi; olculemezse None (aday sona duser)."""
    try:
        c, atr = act.get("signal_close"), act.get("atr14")
        esik = act.get("ref_close") if act.get("ref_close") is not None else act.get("ema200")
        if c is None or atr is None or esik is None or float(atr) <= 0:
            return None
        return (float(c) - float(esik)) / float(atr)
    except Exception:
        return None


def _sira_koru(_sym, _act):
    return 0.0                                      # kararli siralama -> arsiv sirasi korunur


def _funding(fc):
    if not isinstance(fc, dict):
        return "KAPSAM_YOK"
    q, a = int(fc.get("queries") or 0), int(fc.get("answered") or 0)
    if q == 0:
        return "SORULMADI"
    return "TAM(%d/%d)" % (a, q) if a == q else "EKSIK(%d/%d)" % (a, q)


# YALNIZ EKSIK HUCRELER
CELLS = [("10B", TEN, _sira_koru), ("10C", TEN, _guc)]


def main():
    import analyze
    import momentum_rules
    import run_rule
    import strategy_rules

    for wname, dfrom, dto in WINDOWS:
        for rule in ("m2_tsmom28", "t2_trend_regime"):
            for kol, syms, key in CELLS:
                rid = "gr_%s_%s_%s" % (rule.split("_")[0], wname.lower(), kol.lower())
                if rid in DONE:
                    print("%-3s %-16s %-4s -> ATLANDI" % (wname, rule, kol), flush=True)
                    continue
                row = {"pencere": wname, "rule": rule, "kol": kol, "run_id": rid}
                t0 = time.time()
                try:
                    try:
                        s = strategy_rules.build(rule)
                    except KeyError:
                        s = momentum_rules.build(rule)
                    meta = run_rule.run(rid, None, dfrom, dto, strategy=s, no_breakeven=True,
                                        symbols=syms, cache_dir=ARCHIVE, require_all=False,
                                        candidate_order=key)
                    tr, led = analyze.load(rid)
                    m = analyze.metrics(tr, led)
                    row.update({"ok": True, "n_yuklenen": meta.get("n_symbols_loaded"),
                                "funding": _funding(meta.get("funding_coverage")),
                                "n_opened": meta.get("n_opened"), "n_kapanan": m.get("n"),
                                "islem_goren_coin": m.get("symbols"),
                                "getiri_pct": round(float(m.get("final_equity", 100.0)) - 100.0, 2),
                                "ort_R": m.get("mean_r"), "isabet": m.get("win_rate"),
                                "maks_dd": m.get("max_drawdown_usdt"), "pf": m.get("profit_factor")})
                except Exception as exc:
                    row.update({"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc),
                                "iz": traceback.format_exc()[-700:]})
                row["elapsed_s"] = round(time.time() - t0, 1)
                results.append(row)
                _emit()
                print("%-3s %-16s %-4s -> %s" % (wname, rule, kol,
                      ("getiri=%+8.1f%% kapanan=%-4s coin=%-3s ortR=%-7s dd=%-7s %s" % (
                          row.get("getiri_pct") or 0.0, row.get("n_kapanan"), row.get("islem_goren_coin"),
                          row.get("ort_R"), row.get("maks_dd"), row.get("funding")))
                      if row.get("ok") else "HATA " + str(row.get("hata"))[:120]), flush=True)


if __name__ == "__main__":
    main()
    print("=== BITTI ===")
