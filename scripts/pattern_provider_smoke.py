# -*- coding: utf-8 -*-
"""SALT OKUNUR SAĞLAYICI KONTROLÜ — formasyon trader'ının ihtiyaç duyduğu Binance public uçları.

AYRI KOMUTTUR: normal test suite'in parçası DEĞİLDİR (suite ağsız çalışır). API anahtarı, imza ve emir
YOKTUR; yalnız public GET uçlarına dokunur. Doğrulanan yollar, formasyon defterinin gerçekten kullandığı
yollardır:

    exchangeInfo   → sembol metadata + PRICE_FILTER/LOT_SIZE/MIN_NOTIONAL (`universe.discover`, `filters.refresh_futures_filters`)
    klines         → 15m / 1h / 4h kapalı mum (`pattern_trader.data.DataService`)
    premiumIndex   → güncel perp mark + borsanın zaman damgası (`pattern_trader.data.PriceService`, `strategy_paper.verified_price`)
    depth / bookTicker → tetik anı likiditesi (`PriceService.liquidity`, evren spread filtresi)
    fundingRate    → GERÇEKLEŞMİŞ settlement oranları (`accounting.funding` tahakkuku)
    fundingInfo    → varsayılan 8 saatten SAPAN sembollerin funding aralığı

Sınırlar (kasıtlı):
* Tek sembol (varsayılan BTC/USDT), her uçtan bir istek — toplam ağırlık küçüktür.
* Fiyat geleceği ya da kârlılık kanıtı DEĞİLDİR: yalnız "bu uç erişilebilir ve şu şekli döndürüyor" kanıtıdır.
* Erişilemeyen uç SENTETİK veriyle ÖRTÜLMEZ: `unreachable` altında gerekçesiyle ayrı raporlanır.

Kullanım:
    python scripts/pattern_provider_smoke.py [--symbol BTC/USDT] [--json <cikti.json>]

Çıkış kodları: 0 hepsi erişildi · 2 hiçbirine erişilemedi (BLOCKED) · 3 kısmi erişim
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.market.http import HttpClient          # noqa: E402
from tradingbot.market.providers import BinanceFuturesProvider  # noqa: E402


def _run(out: dict[str, Any], name: str, fn: Callable[[], dict]) -> None:
    t0 = time.time()
    try:
        v = fn()
    except Exception as exc:  # noqa: BLE001 — erişim engeli sonucu BOZMAZ, ayrı raporlanır
        out["unreachable"][name] = {"ms": round((time.time() - t0) * 1000), "error": f"{type(exc).__name__}: {exc}"[:400]}
        print("ERISILEMEDI  %-22s %s: %s" % (name, type(exc).__name__, str(exc)[:160]))
        return
    out["reached"][name] = {"ms": round((time.time() - t0) * 1000), **v}
    print("ERISILDI     %-22s %s" % (name, json.dumps(v, default=str)[:260]))


def check(symbol: str = "BTC/USDT", *, timeout: float = 15.0) -> dict[str, Any]:
    p = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, timeout=timeout))
    raw = symbol.replace("/", "")
    out: dict[str, Any] = {"checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "base_url": BinanceFuturesProvider.base_url, "symbol": symbol,
                           "reached": {}, "unreachable": {}}

    def _meta() -> dict:
        rows = p.exchange_info() or []
        s = next((r for r in rows if r.get("symbol") == raw), None)
        fl = {f.get("filterType"): f for f in (s or {}).get("filters", [])}
        return {"symbols": len(rows),
                "usdt_perp_trading": sum(1 for r in rows if r.get("contractType") == "PERPETUAL"
                                         and r.get("quoteAsset") == "USDT" and r.get("status") == "TRADING"),
                "tick_size": fl.get("PRICE_FILTER", {}).get("tickSize"),
                "step_size": fl.get("LOT_SIZE", {}).get("stepSize"),
                "min_notional": (fl.get("MIN_NOTIONAL") or fl.get("NOTIONAL") or {}).get("notional"),
                "contract_type": (s or {}).get("contractType"), "onboard_date": (s or {}).get("onboardDate")}

    def _kl(tf: str) -> Callable[[], dict]:
        def f() -> dict:
            df = p.klines(symbol, tf, limit=5)
            return {"rows": int(len(df)),
                    "last_open_ms": int(df["timestamp"].iloc[-1]) if len(df) else None,
                    "last_close": float(df["close"].iloc[-1]) if len(df) else None}
        return f

    def _mark() -> dict:
        m = p.mark_price(symbol)
        return {"mark": m.get("mark"), "last_funding_rate": m.get("funding_rate"),
                "next_funding_ts": m.get("next_funding_ts"), "exchange_ts": m.get("ts")}

    def _depth() -> dict:
        d = p.depth(symbol, limit=20)
        b, a = d.get("bids") or [], d.get("asks") or []
        return {"bids": len(b), "asks": len(a), "best_bid": b[0][0] if b else None, "best_ask": a[0][0] if a else None}

    def _book() -> dict:
        rows = p.book_tickers() or []
        r = next((x for x in rows if x.get("symbol") == raw), None)
        return {"symbols": len(rows), "bid": (r or {}).get("bidPrice"), "ask": (r or {}).get("askPrice")}

    def _fh() -> dict:
        rows = p.funding_history(symbol, limit=5) or []
        return {"rows": len(rows), "last": rows[-1] if rows else None}

    def _fi() -> dict:
        rows = p.funding_info() or []
        dev = [{"symbol": r.get("symbol"), "hours": r.get("fundingIntervalHours")} for r in rows
               if str(r.get("fundingIntervalHours") or "") not in ("", "8")]
        return {"deviating_symbols": len(rows), "non_default_interval_sample": dev[:5],
                "note_tr": "listede OLMAYAN sembol 'veri yok' değil 'varsayılan 8 saat' demektir"}

    _run(out, "exchange_info", _meta)
    for tf in ("15m", "1h", "4h"):
        _run(out, "klines_%s" % tf, _kl(tf))
    _run(out, "mark_price", _mark)
    _run(out, "depth", _depth)
    _run(out, "book_tickers", _book)
    _run(out, "funding_history", _fh)
    _run(out, "funding_info", _fi)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--json", default=None, help="sonucu bu dosyaya yaz")
    ap.add_argument("--timeout", type=float, default=15.0)
    a = ap.parse_args()
    res = check(a.symbol, timeout=a.timeout)
    n_ok, n_bad = len(res["reached"]), len(res["unreachable"])
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("\nerisildi=%d erisilemedi=%d" % (n_ok, n_bad))
    if n_ok == 0:
        print("BLOCKED — hicbir public uca erisilemedi; bu bir KOD hatasi degil ERISIM engelidir.")
        return 2
    return 0 if n_bad == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
