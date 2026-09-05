"""Kârlılık deneyi kalıcılığı — ekle-yalnız olay defteri + atomik/checksum'lı kitap anlık görüntüsü.

**İZOLE DURUM.** Yalnız kendi dosyalarına yazar:

* `profitability_experiment_events.jsonl` — ekle-yalnız olay defteri (kanonik kayıt)
* `profitability_experiment_books.json`   — türetilmiş kitap anlık görüntüsü (atomik)
* `profitability_experiment.json`         — karşılaştırma raporu (atomik)

Kanonik defter, RiskEngine, muhasebe, gateway ve sermaye durumu bu modül tarafından
**HİÇBİR KOŞULDA** okunmaz-yazılmaz.

**Çökme kurtarma:** olay defteri kanoniktir. Kitap anlık görüntüsü her zaman olaylardan
YENİDEN ÜRETİLEBİLİR; anlık görüntü bozuksa/eksikse `replay()` defterden kurar. Aynı olay
kimliği ikinci kez uygulanmaz (idempotent).

**Bozuk satır GİZLENMEZ:** ayrıştırılamayan satırlar sayılır ve raporlanır.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core import atomic_write_json, iso, utc_now
from .profitability_experiment import (POLICIES, ExperimentConfig, PolicyBook, SimClose,
                                       SimPosition)

log = logging.getLogger(__name__)

SCHEMA_VERSION = "profitability_store_v1"

EVENTS_FILE = "profitability_experiment_events.jsonl"
BOOKS_FILE = "profitability_experiment_books.json"
REPORT_FILE = "profitability_experiment.json"

# --- olay türleri --------------------------------------------------------------------------
EV_DECISION = "entry_decision"
EV_OPEN = "open"
EV_MARK = "mark"
EV_CLOSE = "close"
EV_KINDS = (EV_DECISION, EV_OPEN, EV_MARK, EV_CLOSE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExperimentStore:
    """Ekle-yalnız olay defteri + atomik kitap anlık görüntüsü."""

    _locks: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    @classmethod
    def _lock_for(cls, p: Path) -> threading.Lock:
        key = str(p.resolve() if p.is_absolute() else p)
        with cls._guard:
            lk = cls._locks.get(key)
            if lk is None:
                lk = cls._locks[key] = threading.Lock()
            return lk

    def __init__(self, state_dir: Path | str, *, max_events_per_cycle: int = 500,
                 max_lines: int = 0, archive: Any = None) -> None:
        self.dir = Path(state_dir)
        self.events_path = self.dir / EVENTS_FILE
        self.books_path = self.dir / BOOKS_FILE
        self.report_path = self.dir / REPORT_FILE
        self._lock = self._lock_for(self.events_path)
        self.max_events_per_cycle = int(max_events_per_cycle)
        #: 0 → rotasyon YOK (sınırsız büyür, KAYIP YOK).
        self.max_lines = max(0, int(max_lines))
        #: Arşiv yoksa BUDAMA da yok (arşiv-önce sözleşmesi).
        self.archive = archive
        self.appended = 0
        self.duplicates = 0
        self.errors = 0
        self.malformed = 0
        self._ids: set[str] | None = None

    # ------------------------------------------------------------------ okuma
    def iter_events(self) -> Iterable[dict[str, Any]]:
        """Ekle-yalnız defteri satır satır okur. Bozuk satır SAYILIR, sessizce atlanmaz."""
        if not self.events_path.exists():
            return
        # Her tarama TAM yeniden okumadır; sayaç birikmemeli.
        self.malformed = 0
        try:
            text = self.events_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.errors += 1
            log.warning("deney olay defteri okunamadı: %s", exc)
            return
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                self.malformed += 1
                continue
            if isinstance(d, dict):
                yield d
            else:
                self.malformed += 1

    def known_event_ids(self) -> set[str]:
        if self._ids is None:
            self._ids = {str(e.get("event_id")) for e in self.iter_events()
                         if e.get("event_id")}
        return self._ids

    # ------------------------------------------------------------------ yazma
    def append(self, event: dict[str, Any]) -> bool:
        """Tek olayı ekler. Aynı `event_id` İKİNCİ KEZ yazılmaz (idempotent)."""
        eid = str((event or {}).get("event_id") or "")
        if not eid:
            self.errors += 1
            return False
        with self._lock:
            ids = self.known_event_ids()
            if eid in ids:
                self.duplicates += 1
                return False
            try:
                line = json.dumps(event, ensure_ascii=False, allow_nan=False, default=str)
            except (TypeError, ValueError) as exc:
                self.errors += 1
                log.warning("deney olayı serileştirilemedi (%s): %s", eid, exc)
                return False
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.events_path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
            except OSError as exc:
                self.errors += 1
                log.warning("deney olayı yazılamadı (%s): %s", eid, exc)
                return False
            ids.add(eid)
            self.appended += 1
            return True

    def append_many(self, events: Iterable[dict[str, Any]]) -> dict[str, int]:
        w = d = 0
        for i, e in enumerate(events):
            if i >= self.max_events_per_cycle:
                log.warning("deney olay tavanı aşıldı (%d); kalanlar bir sonraki tura",
                            self.max_events_per_cycle)
                break
            if self.append(e):
                w += 1
            else:
                d += 1
        return {"written": w, "skipped": d, "duplicates": self.duplicates,
                "errors": self.errors, "malformed": self.malformed}

    # ------------------------------------------------------------------ replay
    def replay(self, cfg: ExperimentConfig) -> dict[str, PolicyBook]:
        """Olay defterinden kitapları YENİDEN KURAR. Deterministik ve idempotenttir.

        Aynı defter iki kez oynatılırsa AYNI kitap çıkar: her olay `event_id` ile
        tekilleştirilir ve olaylar dosya sırasına göre uygulanır.
        """
        books = {p: PolicyBook(p) for p in POLICIES}
        seen: set[str] = set()
        for e in self.iter_events():
            eid = str(e.get("event_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            if str(e.get("experiment_id")) != cfg.experiment_id:
                continue
            pol = str(e.get("policy") or "")
            b = books.get(pol)
            if b is None:
                continue
            kind = str(e.get("kind") or "")
            pl = e.get("payload") or {}
            try:
                if kind == EV_DECISION:
                    d = str(pl.get("decision") or "")
                    if d == "ACCEPT":
                        b.n_accept += 1
                    elif d == "FILTER":
                        b.n_filter += 1
                    elif d == "ABSTAIN":
                        b.n_abstain += 1
                elif kind == EV_OPEN:
                    pos = pl.get("position") or {}
                    allowed = {f.name for f in SimPosition.__dataclass_fields__.values()}
                    b.positions[str(pos.get("trade_id"))] = SimPosition(
                        **{k: v for k, v in pos.items() if k in allowed})
                    r = pl.get("returns_1h")
                    if isinstance(r, list):
                        b.returns[str(pos.get("symbol"))] = r
                elif kind == EV_MARK:
                    from .profitability_experiment import apply_mark
                    apply_mark(b, str(pl.get("trade_id")), pl.get("mark"))
                elif kind == EV_CLOSE:
                    c = pl.get("close") or {}
                    allowed = {f.name for f in SimClose.__dataclass_fields__.values()}
                    b.positions.pop(str(c.get("trade_id")), None)
                    b.closes.append(SimClose(**{k: v for k, v in c.items() if k in allowed}))
            except (TypeError, ValueError, KeyError) as exc:
                self.errors += 1
                log.warning("deney olayı uygulanamadı (%s/%s): %s", pol, kind, exc)
        return books

    # ------------------------------------------------------------------ anlık görüntü
    def save_books(self, books: dict[str, PolicyBook], cfg: ExperimentConfig) -> dict[str, Any]:
        """Kitapları ATOMİK + checksum'lı yazar. Bozulursa `replay()` kanonik kaynaktır."""
        payload = {"schema_version": SCHEMA_VERSION, **cfg.identity(),
                   "written_at": iso(utc_now()),
                   "books": {p: b.to_dict() for p, b in sorted(books.items())}}
        blob = json.dumps(payload["books"], sort_keys=True, ensure_ascii=False, default=str)
        payload["checksum_sha256"] = _sha256(blob)
        payload["event_count"] = len(self.known_event_ids())
        try:
            atomic_write_json(self.books_path, payload)
            return {"ok": True, "checksum": payload["checksum_sha256"],
                    "path": str(self.books_path)}
        except Exception as exc:  # noqa: BLE001 — anlık görüntü arızası turu DURDURMAZ
            self.errors += 1
            log.warning("deney kitabı yazılamadı: %s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}"}

    def load_books(self, cfg: ExperimentConfig) -> tuple[dict[str, PolicyBook], dict[str, Any]]:
        """Anlık görüntüyü yükler ve checksum'ı DOĞRULAR; bozuksa defterden replay eder."""
        meta: dict[str, Any] = {"source": None, "checksum_ok": None}
        if self.books_path.exists():
            try:
                d = json.loads(self.books_path.read_text(encoding="utf-8"))
                bd = d.get("books") or {}
                blob = json.dumps(bd, sort_keys=True, ensure_ascii=False, default=str)
                ok = (_sha256(blob) == d.get("checksum_sha256"))
                meta["checksum_ok"] = ok
                if ok and str(d.get("config_id")) == cfg.config_id:
                    books = {p: PolicyBook(p) for p in POLICIES}
                    for p, v in bd.items():
                        if p in books:
                            books[p] = PolicyBook.from_dict(v)
                    meta["source"] = "SNAPSHOT"
                    return books, meta
                meta["reason"] = ("CHECKSUM_MISMATCH" if not ok else "CONFIG_ID_CHANGED")
            except (OSError, ValueError, TypeError) as exc:
                meta["reason"] = f"UNREADABLE:{type(exc).__name__}"
        meta["source"] = "REPLAY"
        return self.replay(cfg), meta

    def stats(self) -> dict[str, Any]:
        n = len(self.known_event_ids())
        return {"schema_version": SCHEMA_VERSION, "events": n,
                "appended": self.appended, "duplicates": self.duplicates,
                "errors": self.errors, "malformed": self.malformed,
                "max_lines": self.max_lines,
                "retention_policy": ("NO_ARCHIVE_NO_DELETION" if not self.archive
                                     else "ARCHIVE_FIRST"),
                "silent_deletion": False,
                "events_path": str(self.events_path),
                "books_path": str(self.books_path)}

    # ------------------------------------------------------------------ rotasyon
    def rotate(self) -> dict[str, Any]:
        """ARŞİV-ÖNCE rotasyon. Arşiv yoksa ya da yazılamazsa **BUDAMA YAPILMAZ**."""
        if self.max_lines <= 0 or self.archive is None:
            return {"archived": 0, "trimmed": 0, "health": "DISABLED_NO_DELETION"}
        try:
            lines = [ln for ln in self.events_path.read_text(
                encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError as exc:
            self.errors += 1
            return {"archived": 0, "trimmed": 0, "health": "READ_FAILED",
                    "error": type(exc).__name__}
        if len(lines) <= self.max_lines:
            return {"archived": 0, "trimmed": 0, "health": "OK", "hot_lines": len(lines)}
        head = lines[:len(lines) - self.max_lines]
        try:
            seg = self.archive.seal(head)          # ÖNCE arşive mühürle
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            log.warning("deney arşivi yazılamadı — BUDAMA YOK: %s", exc)
            return {"archived": 0, "trimmed": 0, "health": "ARCHIVE_FAILED",
                    "error": type(exc).__name__}
        try:
            rest = lines[len(head):]
            tmp = self.events_path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(rest) + "\n", encoding="utf-8", newline="\n")
            os.replace(tmp, self.events_path)
            self._ids = None
        except OSError as exc:
            self.errors += 1
            return {"archived": len(head), "trimmed": 0, "health": "TRIM_FAILED",
                    "error": type(exc).__name__, "segment": str(seg)}
        return {"archived": len(head), "trimmed": len(head), "health": "OK",
                "hot_lines": len(rest), "segment": str(seg)}


__all__ = ["SCHEMA_VERSION", "EVENTS_FILE", "BOOKS_FILE", "REPORT_FILE",
           "EV_DECISION", "EV_OPEN", "EV_MARK", "EV_CLOSE", "EV_KINDS", "ExperimentStore"]
