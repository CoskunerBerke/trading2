# -*- coding: utf-8 -*-
"""SİNYAL LABORATUVARI — komut satırı. Binance USDⓈ-M geçmiş mumlarını indirir (önbelleğe), bütün sinyalleri geçmişte
adım adım tarar, maliyet sonrası sonucu keşif/doğrulama dönemlerine ayırıp raporlar. Salt araştırma: botlara dokunmaz.

    python scripts/signal_lab.py                         # varsayılan: 12 coin · 5m,15m,1h,4h
    python scripts/signal_lab.py --tfs 1h,4h --symbols BTC/USDT,ETH/USDT --jobs 4
    python scripts/signal_lab.py --tfs 4h --days 4h=1460   # daha uzun geçmiş
    python scripts/signal_lab.py --symbols genis --tfs 1h,4h --days 1h=730,4h=1460   # sağlamlık: 30 coin, uzun geçmiş

Çıktı: <out>/signal_lab_report.json (bütün kombinasyonlar) + <out>/signal_lab_events.csv.gz (her işlem).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot import signal_lab as L  # noqa: E402


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def provider_factory():
    from tradingbot.market.http import HttpClient
    from tradingbot.market.providers import BinanceFuturesProvider
    from tradingbot.market.ratelimit import BudgetPool
    return BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, BudgetPool(safety=0.5).get("fapi.binance.com"),
                                             timeout=15.0, max_retries=4))


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Sinyal laboratuvarı (geçmiş test, maliyet sonrası, keşif/doğrulama).")
    ap.add_argument("--symbols", default=",".join(L.DEFAULT_SYMBOLS))
    ap.add_argument("--tfs", default=",".join(L.DEFAULT_TFS))
    ap.add_argument("--days", default="", help="ör. 15m=180,1h=365 (varsayılanları ezer)")
    ap.add_argument("--cache", default=str(ROOT / "signal_lab_data"), help="mum önbelleği klasörü")
    ap.add_argument("--out", default=str(ROOT / "signal_lab_out"), help="rapor klasörü")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--no-catalog", action="store_true", help="yalnız ek varyasyonlar (hızlı deneme)")
    ap.add_argument("--offline", action="store_true", help="indirme yok; yalnız önbellek")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)
    days = {}
    for part in filter(None, a.days.split(",")):
        k, v = part.split("=")
        days[k.strip()] = int(v)
    presets = {"GENIS": L.WIDE_SYMBOLS, "GENİŞ": L.WIDE_SYMBOLS, "VARSAYILAN": L.DEFAULT_SYMBOLS}
    symbols = list(presets.get(a.symbols.strip().upper()) or [s.strip().upper() for s in a.symbols.split(",") if s.strip()])
    tfs = [t.strip() for t in a.tfs.split(",") if t.strip()]
    try:
        report = L.run(symbols=symbols, tfs=tfs, cache_dir=Path(a.cache), out_dir=Path(a.out), cfg=L.LabConfig(),
                       provider_factory=None if a.offline else provider_factory, days=days, jobs=a.jobs,
                       catalog=not a.no_catalog)
    except ValueError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    print()
    print(L.render(report, top=a.top))
    print(f"\nayrıntı: {Path(a.out) / 'signal_lab_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
