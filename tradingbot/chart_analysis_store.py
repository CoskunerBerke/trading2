# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — analiz anlarının saklanması.

Yerleşim: `state/chart_analysis/<book_id>/<BASE>_<QUOTE>_<tf>/<as_of_ms>_<analysis_id>.json`
İndeks:   `state/chart_analysis/index.json` — seri başına {analysis_id, as_of_ms, last_closed_bar, decision_fingerprint, file}

Kurallar: aynı `analysis_id` ikinci kez YAZILMAZ (tekilleştirme); mevcut kayıt hiçbir koşulda yeniden
yazılmaz (sonraki mumlar geçmişi değiştiremez); seri başına `keep_per_series` üstündeki EN ESKİ kayıtlar
silinir (saklama sınırı; boyut için). Yazma yalnız motor turundan; panel SALT OKUR.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core import atomic_write_json, read_json

DIRNAME = "chart_analysis"
INDEX_FILE = "index.json"


def series_key(book_id: str, symbol: str, timeframe: str) -> str:
    return "%s|%s|%s" % (book_id, symbol, timeframe)


def _series_dir(root: Path, book_id: str, symbol: str, timeframe: str) -> Path:
    safe = str(symbol).replace("/", "_")
    return root / str(book_id) / ("%s_%s" % (safe, timeframe))


class ChartAnalysisStore:
    def __init__(self, state_dir: Path | str, *, keep_per_series: int = 300) -> None:
        self.root = Path(state_dir) / DIRNAME
        self.keep = max(1, int(keep_per_series))

    # ------------------------------------------------------------------ okuma
    def index(self) -> dict[str, Any]:
        d = read_json(self.root / INDEX_FILE, default=None)
        return d if isinstance(d, dict) and isinstance(d.get("series"), dict) else {"schema_version": "chart_analysis_index_v1", "series": {}}

    def list(self, book_id: str, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        rows = self.index()["series"].get(series_key(book_id, symbol, timeframe)) or []
        return sorted(rows, key=lambda r: int(r.get("as_of_ms") or 0))

    def latest(self, book_id: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
        rows = self.list(book_id, symbol, timeframe)
        return self.load(rows[-1]["analysis_id"]) if rows else None

    def load(self, analysis_id: str) -> dict[str, Any] | None:
        aid = str(analysis_id)
        if not aid.isalnum() or len(aid) > 32:
            return None                                   # yol enjeksiyonu yok: kimlik yalnız hex
        for rows in self.index()["series"].values():
            for r in rows:
                if r.get("analysis_id") == aid:
                    p = self.root / str(r.get("file") or "")
                    d = read_json(p, default=None)
                    return d if isinstance(d, dict) else None
        return None

    def has(self, analysis_id: str) -> bool:
        aid = str(analysis_id)
        return any(r.get("analysis_id") == aid for rows in self.index()["series"].values() for r in rows)

    # ------------------------------------------------------------------ yazma (yalnız motor)
    def save(self, snap: dict[str, Any]) -> dict[str, Any]:
        """Yeni analysis_id ise dosyayı ve indeksi yazar; varsa hiçbir şeye dokunmaz. Dönüş: {written, pruned}."""
        ident = snap.get("identity") or {}
        aid = str(snap.get("analysis_id") or "")
        key = series_key(ident.get("book_id", "main"), ident.get("symbol", "?"), ident.get("timeframe", "?"))
        idx = self.index()
        rows = list(idx["series"].get(key) or [])
        if any(r.get("analysis_id") == aid for r in rows):
            return {"written": False, "pruned": 0, "analysis_id": aid}
        sdir = _series_dir(self.root, ident.get("book_id", "main"), ident.get("symbol", "?"), ident.get("timeframe", "?"))
        sdir.mkdir(parents=True, exist_ok=True)
        fname = "%d_%s.json" % (int(ident.get("as_of_ms") or 0), aid)
        path = sdir / fname
        if not path.exists():                           # var olan kayıt ASLA yeniden yazılmaz
            atomic_write_json(path, snap)
        rows.append({"analysis_id": aid, "as_of_ms": int(ident.get("as_of_ms") or 0), "as_of": ident.get("as_of"),
                     "last_closed_bar": (ident.get("last_closed_bar") or {}).get("timestamp"),
                     "decision_fingerprint": snap.get("decision_fingerprint"),
                     "file": str(path.relative_to(self.root)).replace("\\", "/"), "code_sha": ident.get("code_sha")})
        rows.sort(key=lambda r: int(r.get("as_of_ms") or 0))
        pruned = 0
        while len(rows) > self.keep:
            old = rows.pop(0)
            try:
                os.remove(self.root / str(old.get("file")))
                pruned += 1
            except OSError:
                pass
        idx["series"][key] = rows
        atomic_write_json(self.root / INDEX_FILE, idx)
        return {"written": True, "pruned": pruned, "analysis_id": aid}

    def stats(self) -> dict[str, Any]:
        idx = self.index()
        n_files = sum(len(v) for v in idx["series"].values())
        size = 0
        for rows in idx["series"].values():
            for r in rows:
                try:
                    size += os.path.getsize(self.root / str(r.get("file")))
                except OSError:
                    continue
        return {"series": len(idx["series"]), "snapshots": n_files, "bytes": size, "keep_per_series": self.keep}


__all__ = ["ChartAnalysisStore", "DIRNAME", "INDEX_FILE", "series_key"]
