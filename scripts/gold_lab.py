# -*- coding: utf-8 -*-
"""ALTIN LABORATUVARI (gold_v1) — komut satırı. Ön kayıt: docs/GOLD_LAB_V1.md; kurallar ve mühür tradingbot/gold_lab.py
(`GOLD_REGISTRY`, `GOLD_REGISTRY_SHA`). Salt araştırma: botlara, defterlere ve ayarlara dokunmaz.

    python scripts/gold_lab.py                                   # bütün bölümler (main, venue, dukascopy, existing)
    python scripts/gold_lab.py --section main --jobs 3           # yalnız ana seri (PAXGUSDT spot; 32 birincil hücre)
    python scripts/gold_lab.py --section venue                   # Binance vadeli XAUUSDT / PAXGUSDT (bilgi; main gerekir)
    python scripts/gold_lab.py --section dukascopy --duka-root <ayna>   # Dukascopy XAUUSD (kapsama ≥ %95 ise; main gerekir)
    python scripts/gold_lab.py --section existing --jobs 4       # mevcut laboratuvar setleri (KEŞİF)
    python scripts/gold_lab.py --offline                         # indirme yok; yalnız önbellek

Bölümler ayrı koşulabilir: aynı --out klasöründe aynı mühür, ayar ve pencereli önceki rapor varsa yeni bölümler onunla
birleşir (koşulmayan bölümler korunur); farklı mühürlü rapor varsa HATA (başka --out seçin). venue ve dukascopy'nin notları
('mekânda tutmadı', 'uzun geçmişte tutmadı') ana seri hücrelerine dayanır: ana seri aynı koşuda ya da aynı --out'taki önceki
raporda yoksa bu bölümler koşmaz (HATA). Yani önce --section main, sonra aynı --out ile --section venue / dukascopy.

Veri yalnız data.binance.vision arşivinden (REST yok) ve yerel Dukascopy aynasından okunur; fiyat verisi depoya yüklenmez.
Çıktı: <out>/gold_lab_report.json, <out>/gold_lab_report.md, bölüm başına <out>/gold_lab_events_<bölüm>.csv.gz.
"""
from __future__ import annotations

import argparse
import lzma
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot import gold_lab as G  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Altın laboratuvarı gold_v1 (ön kayıtlı; geçmiş test, maliyet sonrası).")
    ap.add_argument("--section", default="all", help="main | venue | dukascopy | existing | all (virgülle birden çok)")
    ap.add_argument("--cache", default=str(ROOT / "gold_lab_data"), help="arşiv önbelleği klasörü")
    ap.add_argument("--out", default=str(ROOT / "gold_lab_out"), help="rapor klasörü")
    ap.add_argument("--duka-root", default=None, help="Dukascopy aynası (varsayılan <cache>/dukascopy)")
    ap.add_argument("--offline", action="store_true", help="indirme yok; yalnız önbellek")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    a = ap.parse_args(argv)
    sections = [s.strip().lower() for s in a.section.split(",") if s.strip()]
    print(f"gold_lab {G.GOLD_VERSION} · mühür GOLD_REGISTRY_SHA = {G.GOLD_REGISTRY_SHA} · belge {G.GOLD_DOC}")
    try:
        report = G.run(sections=sections, cache_dir=Path(a.cache), out_dir=Path(a.out), cfg=L.LabConfig(), offline=a.offline,
                       jobs=a.jobs, duka_root=a.duka_root)
    except (ValueError, OSError, zipfile.BadZipFile, lzma.LZMAError, G.GoldDataError) as exc:   # OSError ⊃ ConnectionError
        print(f"\nHATA ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    print(f"koşulan bölümler: {', '.join(report['sections'])} · {report.get('seconds')} sn")
    if report.get("main"):
        print(f"sonuç: {report['main']['conclusion_tr']}")
    if (report.get("dukascopy") or {}).get("status") == "yapılamadı":
        print(f"Dukascopy: yapılamadı — {report['dukascopy']['tr']}")
    out = Path(a.out)
    evs = " · ".join(str(out / G.events_file(s)) for s in report["sections"])
    print(f"ayrıntı: {out / G.REPORT_JSON} · {out / G.REPORT_MD} · olaylar: {evs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
