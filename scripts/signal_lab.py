# -*- coding: utf-8 -*-
"""SİNYAL LABORATUVARI — komut satırı. Binance USDⓈ-M geçmiş mumlarını indirir (önbelleğe), bütün sinyalleri geçmişte
adım adım tarar, maliyet sonrası sonucu keşif/doğrulama dönemlerine ayırıp raporlar. Salt araştırma: botlara dokunmaz.

    python scripts/signal_lab.py                         # varsayılan: 12 coin · 5m,15m,1h,4h
    python scripts/signal_lab.py --tfs 1h,4h --symbols BTC/USDT,ETH/USDT --jobs 4
    python scripts/signal_lab.py --tfs 4h --days 4h=1460   # daha uzun geçmiş
    python scripts/signal_lab.py --symbols genis --tfs 1h,4h --days 1h=730,4h=1460   # sağlamlık: 30 coin, uzun geçmiş
    python scripts/signal_lab.py --source archive ...    # REST erişimi yoksa: data.binance.vision toplu arşivi
    python scripts/signal_lab.py --only-variations --variations CV001_X --symbols genis --tfs 4h,1h,1d \
        --days 4h=1460,1h=730,1d=1825                      # yalnız mum varyasyonları (docs/CANDLE_VARIATIONS_4H.md)

Çıktı: <out>/signal_lab_report.json (bütün kombinasyonlar) + <out>/signal_lab_events.csv.gz (her işlem)
+ varyasyon koşulduysa <out>/variation_records/<ID>.json (kapının okuduğu kayıt; bayt bayt kopyalanır).
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


def resolve_variations(spec: str, *, warn=print) -> list[str]:
    """`--variations` değeri → kimlik listesi. `all` = örnek OLMAYAN bütün kayıtlar. Bilinmeyen/bozuk kimlik ValueError.
    Çeviri onayı (readback) olmayan kayıt için UYARI: taslak koşar ama kaydı onayı taşımaz ve kapıdan geçmez; onaydan
    sonra laboratuvar yeniden koşulmalıdır."""
    spec = (spec or "").strip()
    if not spec:
        return []
    from tradingbot import candle_variations as CV
    if spec.lower() in ("all", "hepsi"):
        ids = [e["id"] for e in CV.VARIATIONS if isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("example") is not True]
    else:
        ids = [x.strip().upper() for x in spec.split(",") if x.strip()]
    out: list[str] = []
    for vid in ids:
        if vid in out:
            continue
        var = CV.get(vid)
        if var.example:
            warn(f"UYARI: {vid} ÖRNEK kayıt — laboratuvarda koşar, hiçbir zaman işlem açmaz")
        if var.readback is None:
            warn(f"UYARI: {vid} çeviri onayı (readback) YOK — taslak koşu: kaydı kapıdan geçmez (READBACK_AFTER_LAB); "
                 "çeviri onayından sonra laboratuvar yeniden koşulmalı")
        out.append(vid)
    return out


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Sinyal laboratuvarı (geçmiş test, maliyet sonrası, keşif/doğrulama).")
    ap.add_argument("--symbols", default=",".join(L.DEFAULT_SYMBOLS))
    ap.add_argument("--tfs", default=",".join(L.DEFAULT_TFS))
    ap.add_argument("--days", default="", help="ör. 15m=180,1h=365 (varsayılanları ezer)")
    ap.add_argument("--cache", default=str(ROOT / "signal_lab_data"), help="mum önbelleği klasörü")
    ap.add_argument("--out", default=str(ROOT / "signal_lab_out"), help="rapor klasörü")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--no-catalog", action="store_true", help="katalog taraması yok (ek varyasyonlar + algoritmalar; hızlı)")
    ap.add_argument("--no-algos", action="store_true", help="algoritmaları (trend, momentum, RSI2, sıkışma, coinler arası) çalıştırma")
    ap.add_argument("--offline", action="store_true", help="indirme yok; yalnız önbellek")
    ap.add_argument("--source", choices=("api", "archive"), default="api",
                    help="api = fapi.binance.com (güncel); archive = data.binance.vision toplu arşivi (bitmiş günler)")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--variations", default="",
                    help="mum varyasyonları: CV001_X,CV002_Y ya da all (örnek olmayan bütün kayıtlar); taslaklar da koşar; "
                         "yalnız --only-variations ile")
    ap.add_argument("--only-variations", action="store_true",
                    help="yalnız mum varyasyonları + eşleri (katalog, ek sinyaller ve algoritmalar yok)")
    a = ap.parse_args(argv)
    try:
        variations = resolve_variations(a.variations)
    except ValueError as exc:
        print(f"\nHATA: {exc}", file=sys.stderr)
        return 2
    if a.only_variations and not variations:
        print("\nHATA: --only-variations için --variations ile en az bir varyasyon gerekli", file=sys.stderr)
        return 2
    if variations and not a.only_variations:
        # katalog/ek/algoritma olayları keşif/doğrulama kesimini ve plasebo havuzlarını değiştirir; kapı böyle kaydı almaz
        print("\nHATA: --variations yalnız --only-variations ile koşar (katalogla birlikte koşu kesimi değiştirir; "
              "kayıt kapıdan geçmez)", file=sys.stderr)
        return 2
    days = {}
    for part in filter(None, a.days.split(",")):
        k, v = part.split("=")
        days[k.strip()] = int(v)
    presets = {"GENIS": L.WIDE_SYMBOLS, "GENİŞ": L.WIDE_SYMBOLS, "VARSAYILAN": L.DEFAULT_SYMBOLS}
    symbols = list(presets.get(a.symbols.strip().upper()) or [s.strip().upper() for s in a.symbols.split(",") if s.strip()])
    tfs = [t.strip() for t in a.tfs.split(",") if t.strip()]
    try:
        report = L.run(symbols=symbols, tfs=tfs, cache_dir=Path(a.cache), out_dir=Path(a.out), cfg=L.LabConfig(),
                       provider_factory=None if a.offline else (L.ArchiveProvider if a.source == "archive" else provider_factory),
                       days=days, jobs=a.jobs,
                       catalog=not a.no_catalog and not a.only_variations, algos=not a.no_algos and not a.only_variations,
                       variations=variations, extras=not a.only_variations)
    except (ValueError, L.DownloadAborted) as exc:
        print(f"\nHATA: {exc}", file=sys.stderr)
        return 2
    print()
    print(L.render(report, top=a.top))
    print(f"\nayrıntı: {Path(a.out) / 'signal_lab_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
