# -*- coding: utf-8 -*-
"""40 COIN vs 10 COIN — KONTROLLU KARSILASTIRMA (2026-09-19).

Tek degisken EVREN. Ayni pencereler, ayni kadans (4h), ayni tohum, ayni koruma
(no_breakeven=True — canli kagit defterlerle ayni), ayni ARSIV (bn_archive).

ARSIV: gercek Binance API (www.binance.info) — 11 sutunun 11'i, gercek funding
(40/40 sembol), gercek borsa kurallari (symbol_filters.json, 718 sembol).
Onceki TradingView denemesi TERK EDILDI: funding yok, borsa kurallari varsayilan,
sema kaybi (bkz. tv_archive/GECERSIZ-KULLANMA.md).

FUNDING KAPSAMI ZORUNLU DENETLENIR: catismali denetim, `complete: true` degerinin
VAKUMDA uretilebildigini gosterdi (hic pozisyon acilmazsa queries=0, unknown=0 ->
complete=true). Bu yuzden sart `queries > 0 AND answered == queries`.

PENCERE KAPSAMI: P1'de coinlerin yalnizca 22'si, P2'de 27'si, P3'te 40'i listeliydi.
"40 coinde olctuk" cumlesi YALNIZ P3 icin kurulabilir; diger pencereler gercek
sayiyla raporlanir. Islem goren coin sayisi ayrica yazilir — yuklenmek yetmez.

SIRA: P3 once (40 coinin 40'i listeli; asil soru orada).
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
OUT = Path(__file__).resolve().parent / "run40.json"

WINDOWS = [("P3", "2024-09-01", "2026-08-31"),
           ("P2", "2022-09-01", "2024-08-31"),
           ("P1", "2020-11-01", "2022-08-31")]
TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
       "DOGE/USDT", "LINK/USDT", "AAVE/USDT", "AVAX/USDT", "LTC/USDT"]

results = []

# SURDURULEBILIRLIK (2026-09-19): bilgisayar kapanirsa yarim kalan olcum bastan kosmasin.
# Mevcut sonuc dosyasindaki BASARILI run_id'ler atlanir; basarisizlar yeniden denenir.
if OUT.exists():
    try:
        results = [r for r in json.load(io.open(OUT, encoding="utf-8")) if isinstance(r, dict)]
        print("surduruluyor: %d kayit yuklendi (%d basarili)"
              % (len(results), sum(1 for r in results if r.get("ok"))), flush=True)
    except Exception as exc:                        # bozuk dosya olcumu durdurmaz
        print("mevcut sonuc dosyasi okunamadi (%s) — sifirdan" % exc, flush=True)
        results = []

DONE = {r["run_id"] for r in results if r.get("ok")}


def _emit():
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=1, default=str))


def _funding_verdict(fc):
    """Kapsam GERCEKTEN tam mi. `complete` alani tek basina YETMEZ (vakumda true olabilir)."""
    if not isinstance(fc, dict):
        return "KAPSAM_YOK"
    q = int(fc.get("queries") or 0)
    a = int(fc.get("answered") or 0)
    if q == 0:
        return "SORULMADI(queries=0)"
    return "TAM(%d/%d)" % (a, q) if a == q else "EKSIK(%d/%d)" % (a, q)


def main():
    import analyze
    import momentum_rules
    import run_rule
    import strategy_rules

    for wname, dfrom, dto in WINDOWS:
        for rule in ("m2_tsmom28", "t2_trend_regime"):
            for kol, syms in (("40coin", None), ("10coin", TEN)):
                rid = "u40_%s_%s_%s" % (rule.split("_")[0], wname.lower(), kol)
                if rid in DONE:
                    print("%-3s %-16s %-7s -> ATLANDI (zaten basarili)" % (wname, rule, kol), flush=True)
                    continue
                row = {"pencere": wname, "rule": rule, "kol": kol, "run_id": rid,
                       "from": dfrom, "to": dto}
                t0 = time.time()
                try:
                    try:
                        s = strategy_rules.build(rule)
                    except KeyError:
                        s = momentum_rules.build(rule)
                    meta = run_rule.run(rid, None, dfrom, dto, strategy=s, no_breakeven=True,
                                        symbols=syms, cache_dir=ARCHIVE,
                                        require_all=(syms is None))
                    tr, led = analyze.load(rid)
                    m = analyze.metrics(tr, led)
                    row.update({
                        "ok": True,
                        "n_yuklenen": meta.get("n_symbols_loaded"),
                        "evren_tam": meta.get("universe_complete"),
                        "dusenler": meta.get("symbols_skipped"),
                        "funding": _funding_verdict(meta.get("funding_coverage")),
                        "n_opened": meta.get("n_opened"),
                        "n_kapanan": m.get("n"),
                        "islem_goren_coin": m.get("symbols"),
                        "getiri_pct": round(float(m.get("final_equity", 100.0)) - 100.0, 2),
                        "ort_R": m.get("mean_r"), "isabet": m.get("win_rate"),
                        "maks_dd": m.get("max_drawdown_usdt"), "pf": m.get("profit_factor"),
                        "funding_toplam": m.get("funding_total"), "ucret_toplam": m.get("fees_total"),
                        "history_root": meta.get("history_root"),
                    })
                except Exception as exc:
                    row.update({"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc),
                                "iz": traceback.format_exc()[-700:]})
                row["elapsed_s"] = round(time.time() - t0, 1)
                results.append(row)
                _emit()
                print("%-3s %-16s %-7s -> %s" % (
                    wname, rule, kol,
                    ("getiri=%+8.1f%% kapanan=%-4s coin=%-3s ortR=%-7s dd=%-7s funding=%s/%s" % (
                        row.get("getiri_pct") or 0.0, row.get("n_kapanan"), row.get("islem_goren_coin"),
                        row.get("ort_R"), row.get("maks_dd"), row.get("funding_toplam"), row.get("funding")))
                    if row.get("ok") else ("HATA " + str(row.get("hata"))[:130])), flush=True)


if __name__ == "__main__":
    main()
    print("=== BITTI ===")
