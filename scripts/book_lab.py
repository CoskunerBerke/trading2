# -*- coding: utf-8 -*-
"""DEFTER ARAŞTIRMASI (book_v1) — komut satırı. Ön kayıt: docs/BOOK_RESEARCH_V1.md; kurallar ve mühür
tradingbot/book_lab.py (`BOOK_REGISTRY`, `BOOK_REGISTRY_SHA`). Salt araştırma: defterlere, stratejilere, config'e ve çalışma
zamanına dokunmaz; PAPER/geçmiş test.

    python scripts/book_lab.py --cache <önbellek> --out <çıktı> --jobs 8       # birincil evren (23 coin), D4 + C4 + Formasyon
    python scripts/book_lab.py --books D4 ...                                   # yalnız D4 işlemleri üretilir; aynı --out'taki
                                                                                # aynı mühürlü diğer defter işlemleri korunur
    python scripts/book_lab.py --filters symbol_filters.json --report-only      # filtre tablosu sonradan gelince kapasite
    python scripts/book_lab.py --universe info                                  # bilgi evreni (40 coin; hükme girmez)
    python scripts/book_lab.py --universe pit                                   # PIT teyidi (§8.5; aynı --out)
    python scripts/book_lab.py --offline                                        # indirme yok; yalnız önbellek

Veri yalnız data.binance.vision arşivinden (REST yok); fiyat verisi depoya yüklenmez. Her koşu denemesi (başarısız ve yarıda
kalanlar dahil) <out>/book_lab_attempts.jsonl'e yazılır (§0.7): başlangıç anı, kod commit'i, mühür, çıkış durumu, hata iletisi.
Çıktı: <out>/book_lab_report[_<evren>].json/.md, book_lab_trades[_<evren>].csv.gz, book_lab_meta[_<evren>].json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot import book_lab as B  # noqa: E402

ATTEMPTS = "book_lab_attempts.jsonl"


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _attempt(out: Path, rec: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / ATTEMPTS).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Defter araştırması book_v1 (ön kayıtlı; geçmiş test, maliyet sonrası).")
    ap.add_argument("--cache", default=str(ROOT / "book_lab_data"), help="arşiv önbelleği klasörü")
    ap.add_argument("--out", default=str(ROOT / "book_lab_out"), help="rapor klasörü")
    ap.add_argument("--offline", action="store_true", help="indirme yok; yalnız önbellek")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--books", default="D4,C4,FM", help="D4, C4, FM (virgülle)")
    ap.add_argument("--universe", default="primary", choices=list(B.UNIVERSES), help="primary (hüküm) | info (bilgi) | pit (teyit)")
    ap.add_argument("--filters", default=None, help="botun FiltersCache JSON'u (VPS data/symbol_filters.json; salt okunur kopya)")
    ap.add_argument("--report-only", action="store_true", help="veri/olay üretimi yok; kayıtlı işlemlerden rapor")
    a = ap.parse_args(argv)
    books = [b.strip().upper() for b in a.books.split(",") if b.strip()]
    out = Path(a.out)
    att = {"attempt": uuid.uuid4().hex[:12], "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "commit": os.environ.get("GITHUB_SHA") or B.git_commit(ROOT), "registry_sha": B.BOOK_REGISTRY_SHA, "version": B.VERSION,
           "argv": list(sys.argv[1:] if argv is None else argv)}
    _attempt(out, {**att, "status": "STARTED"})
    print(f"book_lab {B.VERSION} · mühür BOOK_REGISTRY_SHA = {B.BOOK_REGISTRY_SHA} · belge {B.DOC} · evren {a.universe} · "
          f"defterler {','.join(books)}")
    try:
        rep = B.run(cache_dir=Path(a.cache), out_dir=out, universe=a.universe, books=books, filters_path=a.filters,
                    offline=a.offline, jobs=a.jobs, report_only=a.report_only)
    except (ValueError, OSError, zipfile.BadZipFile, B.BookDataError) as exc:   # OSError ⊃ ConnectionError
        _attempt(out, {**att, "status": "ERROR", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "error": f"{type(exc).__name__}: {exc}"})
        print(f"\nHATA ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    except BaseException as exc:  # noqa: BLE001 — yarıda kalan deneme de kaydedilir
        _attempt(out, {**att, "status": "CRASH", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]})
        raise
    _attempt(out, {**att, "status": "OK", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "trades_sha256": rep.get("trades_sha256"), "seconds": rep.get("seconds")})
    print(f"sonuç: {rep.get('conclusion_tr')}")
    sfx = B._suffix(a.universe)
    print(f"ayrıntı: {out / B.REPORT_JSON.format(suffix=sfx)} · {out / B.REPORT_MD.format(suffix=sfx)} · "
          f"işlemler {out / B.TRADES_FILE.format(suffix=sfx)} · denemeler {out / ATTEMPTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
