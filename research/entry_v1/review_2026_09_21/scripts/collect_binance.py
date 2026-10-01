# -*- coding: utf-8 -*-
"""40 COINLIK ARSIVI GERCEK BINANCE API'SINDEN TOPLA (2026-09-19).

NEDEN BU, `collect_tv.py` DEGIL
--------------------------------
Once TradingView yolu denendi (BINANCE:<SYM>.P). Catismali denetim o yolda 12 kusur
dogruladi, ucu plani GECERSIZ kiliyordu:
  1. funding serisi YOK -> replay her pozisyonda funding=0 uretir, HATA VERMEDEN.
     Olculen buyukluk (eski kosulardan): T2 P1 -66,43 USDT, M2 P1 -22,10; P3'te bile
     T2'nin 11,99 USDT gerceklesmis karinda 2,75 USDT funding var (~%23 sisme).
  2. symbol_filters.json YOK -> 40 coinin 40'i VARSAYILAN borsa kurallarina duser.
     Bu, projenin tum replay sonuclarini bir kez gecersiz kilan kusurun aynisi
     (DOGE tick 0,01 vs gercek 0,00001).
  3. Sema kaybi: TV 11 sutunun 6'sini verir; kalan 5'i NaN olur ve `validate()` bunu
     GOREMEZ (5/11 sutunu bos arsiv 59/59 "ok" alir). Ustelik manifest kendini
     `provider="binance"` ilan eder — kalici bir provenans yalani.

COZUM: `www.binance.info` bu makineden ERISILEBILIR (olculdu 2026-09-19).
fapi.binance.com / api.binance.com / api1 / api-gcp / data-api / data.binance.vision
yedisi de TCP seviyesinde sifirlaniyor (WinError 10054), ama `www.binance.info`
ayni `/fapi/v1/*` uclarini veriyor: exchangeInfo (905 sembol), klines (12 sutun,
sayfalamayla tam derinlik), fundingRate. Yani GERCEK veri, tam sema, gercek kurallar.

Bu betik ucunu de toplar:
  - klines  -> 11 sutunun 11'i (Binance ham kline ile birebir eslesir)
  - funding -> ['timestamp','rate','mark']
  - symbol_filters.json -> mevcut dogrulanmis dosyadan kopyalanir (718 sembol,
    40 coinin 40'i var, degerleri binance.info exchangeInfo ile eslesiyor)

SINIR: hiz limiti. limit=1000 (agirlik 5) + 0,25 sn bekleme ile ~1500 agirlik/dk,
fapi tavani 2400. Ban riski dusuk tutulur; yine de tek seferde 5m toplanmaz.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WT = Path(r"C:/Users/berke/wt-entry")
ROOT = Path(r"C:/Users/berke/research/entry_v1")
sys.path.insert(0, str(WT))
sys.path.insert(0, str(ROOT))

BASE = "https://www.binance.info"
UA = {"User-Agent": "Mozilla/5.0"}
FILTERS_SRC = Path(r"C:/Users/berke/wt-ten/data/symbol_filters.json")

KLINE_COLS = ["timestamp", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
FUNDING_COLS = ["timestamp", "rate", "mark"]
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
         "4h": 14_400_000, "1d": 86_400_000}


def _get(path: str, timeout: float = 25.0, retries: int = 4):
    last = None
    for i in range(retries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(BASE + path, headers=UA), timeout=timeout)
            return json.load(r)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("istek basarisiz (%d deneme): %s -> %s" % (retries, path, last))


def klines(sym: str, tf: str, start_ms: int, end_ms: int, sleep_s: float = 0.25) -> list:
    """SAYFALAMALI tam derinlik. Binance bir cagrida en fazla 1500 bar verir."""
    out, cursor, step = [], start_ms, TF_MS[tf]
    while cursor < end_ms:
        d = _get("/fapi/v1/klines?symbol=%s&interval=%s&startTime=%d&limit=1000" % (sym, tf, cursor))
        if not d:
            break
        out.extend(d)
        nxt = int(d[-1][0]) + step
        if nxt <= cursor:                       # ilerlemiyorsa sonsuz donguyu KES
            break
        cursor = nxt
        if len(d) < 1000:                       # son sayfa
            break
        time.sleep(sleep_s)
    return out


def funding(sym: str, start_ms: int, end_ms: int, sleep_s: float = 0.25) -> list:
    out, cursor = [], start_ms
    while cursor < end_ms:
        d = _get("/fapi/v1/fundingRate?symbol=%s&startTime=%d&limit=1000" % (sym, cursor))
        if not d:
            break
        out.extend(d)
        nxt = int(d[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        if len(d) < 1000:
            break
        time.sleep(sleep_s)
    return out


def collect(dest: str, timeframes: list[str], date_from: str, date_to: str,
            symbols: list[str] | None = None, with_funding: bool = True) -> dict:
    import datetime as dt

    import pandas as pd

    from tradingbot.history import HistoryStore
    from universe import config_universe

    syms = list(symbols) if symbols else config_universe()
    start_ms = int(dt.datetime.fromisoformat(date_from).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = int(dt.datetime.fromisoformat(date_to).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    dest_p = Path(dest)
    store = HistoryStore(dest_p / "history")
    rows = []

    # BORSA KURALLARI: dogrulanmis dosyayi arsiv koküne kopyala. Bu OLMADAN replay
    # varsayilan tick/step/min_notional kullanir ve sonuclar gecersizdir.
    dest_p.mkdir(parents=True, exist_ok=True)
    if FILTERS_SRC.exists():
        shutil.copy2(FILTERS_SRC, dest_p / "symbol_filters.json")
        _f = json.load(io.open(dest_p / "symbol_filters.json", encoding="utf-8"))
        eksik = [s for s in syms if s not in (_f.get("futures") or {})]
        print("symbol_filters.json kopyalandi (verified_at=%s); evrende eksik filtre: %d %s"
              % (_f.get("verified_at"), len(eksik), eksik or ""), flush=True)
    else:
        print("UYARI: symbol_filters.json KAYNAGI YOK -> replay VARSAYILAN kurallara duser", flush=True)

    for i, sym in enumerate(syms, 1):
        raw = sym.replace("/", "")
        for tf in timeframes:
            rec = {"symbol": sym, "kind": tf}
            t0 = time.time()
            try:
                k = klines(raw, tf, start_ms, end_ms)
                if not k:
                    rec.update({"ok": False, "reason": "BOS", "bars": 0})
                else:
                    df = pd.DataFrame([r[:11] for r in k], columns=KLINE_COLS).astype(float)
                    df["timestamp"] = df["timestamp"].astype("int64")
                    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
                    res = store.write("futures", sym, tf, df, source="binance_fapi:www.binance.info")
                    rec.update({"ok": True, "bars": int(len(df)), "rows_new": res.get("rows_new"),
                                "ilk": str(pd.to_datetime(int(df["timestamp"].iloc[0]), unit="ms").date()),
                                "son": str(pd.to_datetime(int(df["timestamp"].iloc[-1]), unit="ms").date())})
            except Exception as exc:  # noqa: BLE001
                rec.update({"ok": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:110]), "bars": 0})
            rec["elapsed_s"] = round(time.time() - t0, 1)
            rows.append(rec)
            print("[%2d/%d] %-14s %-3s %s" % (i, len(syms), sym, tf,
                  ("%6d bar %s..%s" % (rec.get("bars"), rec.get("ilk"), rec.get("son")))
                  if rec.get("ok") else "BASARISIZ " + str(rec.get("reason"))[:60]), flush=True)

        if with_funding:
            rec = {"symbol": sym, "kind": "funding"}
            t0 = time.time()
            try:
                fr = funding(raw, start_ms, end_ms)
                if not fr:
                    rec.update({"ok": False, "reason": "BOS", "bars": 0})
                else:
                    df = pd.DataFrame([{"timestamp": int(x["fundingTime"]),
                                        "rate": float(x["fundingRate"]),
                                        "mark": float(x.get("markPrice") or "nan")} for x in fr],
                                      columns=FUNDING_COLS)
                    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
                    res = store.write("futures", sym, "funding", df, source="binance_fapi:www.binance.info")
                    rec.update({"ok": True, "bars": int(len(df)), "rows_new": res.get("rows_new"),
                                "ilk": str(pd.to_datetime(int(df["timestamp"].iloc[0]), unit="ms").date()),
                                "son": str(pd.to_datetime(int(df["timestamp"].iloc[-1]), unit="ms").date())})
            except Exception as exc:  # noqa: BLE001
                rec.update({"ok": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:110]), "bars": 0})
            rec["elapsed_s"] = round(time.time() - t0, 1)
            rows.append(rec)
            print("[%2d/%d] %-14s fun %s" % (i, len(syms), sym,
                  ("%6d settlement %s..%s" % (rec.get("bars"), rec.get("ilk"), rec.get("son")))
                  if rec.get("ok") else "BASARISIZ " + str(rec.get("reason"))[:60]), flush=True)

    return {"dest": str(dest_p / "history"), "base": BASE, "timeframes": timeframes,
            "from": date_from, "to": date_to, "requested": syms, "rows": rows,
            "failed": [r for r in rows if not r.get("ok")]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=r"C:/Users/berke/research/bn_archive")
    ap.add_argument("--timeframes", nargs="*", default=["1d", "4h"])
    ap.add_argument("--from", dest="f", default="2020-10-01")
    ap.add_argument("--to", default="2026-09-19")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out" / "collect_binance.json"))
    a = ap.parse_args()
    s = collect(a.dest, a.timeframes, a.f, a.to, a.symbols, with_funding=not a.no_funding)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    io.open(a.out, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    print("\n=== %d istek, %d basarisiz ===" % (len(s["rows"]), len(s["failed"])))
    for r in s["failed"]:
        print("   %-14s %-8s %s" % (r["symbol"], r["kind"], r.get("reason")))
    sys.exit(1 if s["failed"] else 0)
