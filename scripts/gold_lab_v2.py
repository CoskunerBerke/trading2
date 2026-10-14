# -*- coding: utf-8 -*-
"""ALTIN LABORATUVARI (gold_v2) — komut satırı. Ön kayıt: docs/GOLD_LAB_V2.md; kurallar ve mühür tradingbot/gold_lab_v2.py
(`GOLD_V2_REGISTRY`, `GOLD_V2_REGISTRY_SHA`). Salt araştırma: botlara, defterlere ve ayarlara dokunmaz.

    python scripts/gold_lab_v2.py --sections calib --jobs 4                      # sentetik rastgele yürüyüş ayarı (gerçek veri YOK)
    python scripts/gold_lab_v2.py --sections A --duka-root <ayna> --duka-log <günlük>   # aile A (Dukascopy 2006–2020; hüküm)
    python scripts/gold_lab_v2.py --sections B                                   # aile B (PAXG spot hafta sonu; hüküm)
    python scripts/gold_lab_v2.py --sections seen,venue --duka-root <ayna> --duka-log <günlük>   # bilgi satırları
    python scripts/gold_lab_v2.py --sections all ... --offline                   # indirme yok; yalnız önbellek

Aile A yalnız ayna işi bitince koşar: aynada .part yok, --duka-log günlüğünde "hourly mirror pass finished" satırı var ve ayna
kökünü kullanan süreç yok; koşu başında ve sonunda manifest + bi5 dosyalarının sha256'sı alınır (değişmişse koşu geçersiz).
Bölümler ayrı koşulabilir: aynı --out klasöründe aynı mühür, ayar ve pencereli önceki rapor varsa yeni bölümler onunla birleşir.
Çıktı: <out>/gold_lab_v2_report.json, <out>/gold_lab_v2_report.md, bölüm başına <out>/gold_lab_v2_events_<bölüm>.csv.gz.
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

from tradingbot import gold_lab_v2 as V  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Altın laboratuvarı gold_v2 (ön kayıtlı; geçmiş test, maliyet ve fonlama vekili sonrası).")
    ap.add_argument("--sections", default="all", help="A | B | seen | venue | calib | all (virgülle birden çok)")
    ap.add_argument("--cache", default=str(ROOT / "gold_lab_data"), help="data.binance.vision arşiv önbelleği (gold_v1 ile ortak)")
    ap.add_argument("--out", default=str(ROOT / "gold_lab_v2_out"), help="rapor klasörü")
    ap.add_argument("--duka-root", default=None, help="Dukascopy saatlik aynası (salt okunur)")
    ap.add_argument("--duka-log", default=None, help="ayna işinin günlüğü ('hourly mirror pass finished' satırı aranır)")
    ap.add_argument("--offline", action="store_true", help="indirme yok; yalnız önbellek")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--calib-worlds", type=int, default=V.CALIB_WORLDS, help="sentetik ayar dünya sayısı (bilgi)")
    a = ap.parse_args(argv)
    sections = [s.strip() for s in a.sections.split(",") if s.strip()]
    print(f"gold_lab_v2 {V.GOLD2_VERSION} · mühür GOLD_V2_REGISTRY_SHA = {V.GOLD_V2_REGISTRY_SHA} · belge {V.GOLD2_DOC}")
    try:
        report = V.run(sections=sections, cache_dir=Path(a.cache), out_dir=Path(a.out), cfg=L.LabConfig(), offline=a.offline,
                       jobs=a.jobs, duka_root=a.duka_root, duka_log=a.duka_log, calib_worlds=a.calib_worlds)
    except (ValueError, OSError, zipfile.BadZipFile, lzma.LZMAError, V.GoldDataError) as exc:   # OSError ⊃ ConnectionError
        print(f"\nHATA ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    print(f"koşulan bölümler: {', '.join(report['sections'])} · {report.get('seconds')} sn")
    for line in (report.get("conclusion") or {}).get("lines") or []:
        print(f"sonuç: {line}")
    out = Path(a.out)
    print(f"ayrıntı: {out / V.REPORT_JSON} · {out / V.REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
