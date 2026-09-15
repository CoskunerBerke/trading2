# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — analiz anlarının saklanması (PİYASA-KESİN; onarım bulgu #2 ve #3B).

Yerleşim (v2): `state/chart_analysis/<book_id>/<market_type>/<BASE>_<QUOTE>_<tf>/<as_of_ms>_<analysis_id>.json`
İndeks (v2):   `state/chart_analysis/index.json` — seri anahtarı `book|market|symbol|tf`; satır:
               {analysis_id, as_of_ms, as_of, last_closed_bar, decision_fingerprint, file, code_sha, market_type}.

Uyumluluk politikası (v1 indeks: anahtar `book|symbol|tf`, satırda piyasa YOK — 2e31926 böyle yazıyordu):
satırın dosyasındaki `identity.market_type` okunur ve satır o piyasanın serisine taşınır; dosya okunamıyorsa ya da
piyasa alanı yoksa satır `?` piyasası altında tutulur ve HİÇBİR piyasa isteğinde listelenmez / son kayıt sayılmaz
(kimlikle `load` yine döner; panel kayıt kimliğini isteğin piyasasıyla ayrıca doğrular). Belirsiz eski kayıt başka
piyasaya ATANMAZ. Kayıt DOSYALARI hiçbir koşulda değiştirilmez; yalnız indeks (bir liste) ilk yazımda v2'ye çevrilir.

Kurallar: aynı `analysis_id` ikinci kez YAZILMAZ (tekilleştirme); mevcut kayıt hiçbir koşulda yeniden yazılmaz
(sonraki mumlar geçmişi değiştiremez); seri başına `keep_per_series` üstündeki EN ESKİ kayıtlar silinir (saklama
sınırı; boyut için). Yazma yalnız motor turundan; panel SALT OKUR.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .core import atomic_write_json, read_json

DIRNAME = "chart_analysis"
INDEX_FILE = "index.json"
INDEX_SCHEMA = "chart_analysis_index_v2"
UNKNOWN_MARKET = "?"
#: root -> (indeks dosyası imzası, v2 indeks). v1 indeksin yükseltilmesi (dosya başına bir okuma) süreç başına bir kez.
_INDEX_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


def series_key(book_id: str, market_type: str | None, symbol: str, timeframe: str) -> str:
    return "%s|%s|%s|%s" % (book_id, market_type or UNKNOWN_MARKET, symbol, timeframe)


def split_key(key: str) -> tuple[str, str, str, str] | None:
    """v2 `book|market|symbol|tf`; v1 `book|symbol|tf` → piyasa `?` (dosyadan çözümlenir)."""
    parts = str(key).split("|")
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    if len(parts) == 3:
        return parts[0], UNKNOWN_MARKET, parts[1], parts[2]
    return None


def _series_dir(root: Path, book_id: str, market_type: str, symbol: str, timeframe: str) -> Path:
    safe = str(symbol).replace("/", "_")
    return root / str(book_id) / str(market_type) / ("%s_%s" % (safe, timeframe))


class ChartAnalysisStore:
    def __init__(self, state_dir: Path | str, *, keep_per_series: int = 300) -> None:
        self.root = Path(state_dir) / DIRNAME
        self.keep = max(1, int(keep_per_series))

    # ------------------------------------------------------------------ okuma
    def _signature(self) -> tuple[int, int] | None:
        try:
            s = os.stat(self.root / INDEX_FILE)
        except OSError:
            return None
        return (int(s.st_mtime_ns), int(s.st_size))

    def _market_from_file(self, rel: Any) -> str:
        """v1 satırı: piyasa yalnız kayıt dosyasının kimliğinden; okunamıyorsa `?` (başka piyasaya atanmaz)."""
        if not rel:
            return UNKNOWN_MARKET
        d = read_json(self.root / str(rel), default=None)
        m = ((d.get("identity") or {}).get("market_type")) if isinstance(d, dict) else None
        return str(m) if m else UNKNOWN_MARKET

    def _normalize(self, d: Any) -> dict[str, Any]:
        if not (isinstance(d, dict) and isinstance(d.get("series"), dict)):
            return {"schema_version": INDEX_SCHEMA, "series": {}}
        out: dict[str, list[dict[str, Any]]] = {}
        for key, rows in d["series"].items():
            parts = split_key(key)
            if parts is None or not isinstance(rows, list):
                continue
            book, market, sym, tf = parts
            for r in rows:
                if not isinstance(r, dict):
                    continue
                m = str(r.get("market_type") or market)
                if m == UNKNOWN_MARKET:
                    m = self._market_from_file(r.get("file"))
                row = dict(r)
                row["market_type"] = m
                out.setdefault(series_key(book, m, sym, tf), []).append(row)
        for k in out:
            out[k].sort(key=lambda r: int(r.get("as_of_ms") or 0))
        upgraded = d.get("schema_version") if d.get("schema_version") != INDEX_SCHEMA else None
        return {"schema_version": INDEX_SCHEMA, "series": out, "upgraded_from": upgraded}

    def index(self) -> dict[str, Any]:
        """v2 indeks (v1 ise bellekte yükseltilir; dosya yazılmaz). Dönüş çağıranın değiştirebileceği kopyadır."""
        sig = self._signature()
        if sig is None:
            return {"schema_version": INDEX_SCHEMA, "series": {}}
        cached = _INDEX_CACHE.get(str(self.root))
        if cached is None or cached[0] != sig:
            idx = self._normalize(read_json(self.root / INDEX_FILE, default=None))
            _INDEX_CACHE[str(self.root)] = (sig, idx)
        else:
            idx = cached[1]
        return {"schema_version": idx["schema_version"], "upgraded_from": idx.get("upgraded_from"),
                "series": {k: list(v) for k, v in idx["series"].items()}}

    def list(self, book_id: str, market_type: str, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        """Seri listesi. Piyasa ZORUNLU: bilinmeyen/`?` piyasa hiçbir kaydı listelemez."""
        if not market_type or str(market_type) == UNKNOWN_MARKET:
            return []
        rows = self.index()["series"].get(series_key(book_id, market_type, symbol, timeframe)) or []
        return sorted(rows, key=lambda r: int(r.get("as_of_ms") or 0))

    def latest(self, book_id: str, market_type: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
        rows = self.list(book_id, market_type, symbol, timeframe)
        return self.load(rows[-1]["analysis_id"]) if rows else None

    def find(self, analysis_id: str) -> tuple[str, dict[str, Any]] | None:
        """(seri anahtarı, indeks satırı) — kimlik yalnız alfasayısal (yol enjeksiyonu yok)."""
        aid = str(analysis_id)
        if not aid.isalnum() or len(aid) > 32:
            return None
        for key, rows in self.index()["series"].items():
            for r in rows:
                if r.get("analysis_id") == aid:
                    return key, r
        return None

    def load(self, analysis_id: str) -> dict[str, Any] | None:
        hit = self.find(analysis_id)
        if hit is None:
            return None
        d = read_json(self.root / str(hit[1].get("file") or ""), default=None)
        return d if isinstance(d, dict) else None

    def has(self, analysis_id: str) -> bool:
        return self.find(analysis_id) is not None

    # ------------------------------------------------------------------ yazma (yalnız motor)
    def save(self, snap: dict[str, Any]) -> dict[str, Any]:
        """Yeni analysis_id ise dosyayı ve indeksi yazar; varsa hiçbir şeye dokunmaz. Dönüş: {written, pruned}."""
        ident = snap.get("identity") or {}
        aid = str(snap.get("analysis_id") or "")
        if not aid.isalnum() or len(aid) > 32:
            return {"written": False, "pruned": 0, "analysis_id": aid, "reason": "INVALID_ID"}
        book = str(ident.get("book_id") or "main")
        market = str(ident.get("market_type") or UNKNOWN_MARKET)
        sym, tf = str(ident.get("symbol") or "?"), str(ident.get("timeframe") or "?")
        key = series_key(book, market, sym, tf)
        idx = self.index()
        if self.has(aid):                                   # aynı kimlik hiçbir seride ikinci kez yazılmaz
            return {"written": False, "pruned": 0, "analysis_id": aid}
        rows = list(idx["series"].get(key) or [])
        sdir = _series_dir(self.root, book, market, sym, tf)
        sdir.mkdir(parents=True, exist_ok=True)
        path = sdir / ("%d_%s.json" % (int(ident.get("as_of_ms") or 0), aid))
        if not path.exists():                               # var olan kayıt ASLA yeniden yazılmaz
            atomic_write_json(path, snap)
        rows.append({"analysis_id": aid, "as_of_ms": int(ident.get("as_of_ms") or 0), "as_of": ident.get("as_of"),
                     "last_closed_bar": (ident.get("last_closed_bar") or {}).get("timestamp"),
                     "decision_fingerprint": snap.get("decision_fingerprint"), "market_type": market,
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
        atomic_write_json(self.root / INDEX_FILE, {"schema_version": INDEX_SCHEMA, "series": idx["series"]})
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
        return {"series": len(idx["series"]), "snapshots": n_files, "bytes": size, "keep_per_series": self.keep,
                "index_schema": idx.get("schema_version"), "upgraded_from": idx.get("upgraded_from")}


__all__ = ["ChartAnalysisStore", "DIRNAME", "INDEX_FILE", "INDEX_SCHEMA", "UNKNOWN_MARKET", "series_key", "split_key"]
