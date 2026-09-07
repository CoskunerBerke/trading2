"""Kârlılık deneyi kalıcılığı — ekle-yalnız olay defteri + atomik/checksum'lı kitap anlık görüntüsü.

**İZOLE DURUM.** Yalnız kendi dosyalarına yazar. Dosya adları deney KİMLİĞİNE bağlıdır:

* `pfexp_v1` (tarihsel, SUPERSEDED — asla yeniden yazılmaz):
  `profitability_experiment_events.jsonl` · `_books.json` · `profitability_experiment.json`
* `pfexp_v1_1` (düzeltilmiş, canlı):
  `profitability_experiment_v1_1_events.jsonl` · `_v1_1_books.json` · `_v1_1.json`
  · `_v1_1_identity.json` (BİR KEZ dondurulan başlangıç anı)

Kanonik defter, RiskEngine, muhasebe, gateway ve sermaye durumu bu modül tarafından
**HİÇBİR KOŞULDA** okunmaz-yazılmaz.

**Kimlik fail-closed:** her olay `experiment_id + policy_version + config_id` üçlüsünü taşır.
Üçlüsü deney yapılandırmasıyla uyuşmayan olay kitaba GİRMEZ (yabancı sayılır ve raporlanır);
kitap anlık görüntüsünün kimliği ya da checksum'ı uyuşmazsa anlık görüntüye güvenilmez ve
kitap defterden yeniden kurulur. Eski sürümün olayı yeni sürümün kitabına ASLA giremez.

**Çökme kurtarma:** olay defteri kanoniktir. Kitap anlık görüntüsü her zaman olaylardan
YENİDEN ÜRETİLEBİLİR; anlık görüntü bozuksa/eksikse `replay()` defterden kurar. Aynı olay
kimliği ikinci kez uygulanmaz (idempotent).

**Bozuk satır GİZLENMEZ:** ayrıştırılamayan satırlar sayılır ve raporlanır.

**Eski kanıt salt okunur:** `legacy_v1_summary()` v1 dosyalarını yalnız OKUR (sha256 ile
birlikte raporlar); v1 dosyalarına hiçbir yol yazmaz.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core import atomic_write_json, iso, utc_now
from .profitability_experiment import (ABSTAIN, ACCEPT, AE_SOURCE_LEGACY, FILTER,
                                       LEGACY_POLICY_VERSION, P1, P4, POLICIES, R_AE_UNKNOWN,
                                       STATUS_SUPERSEDED, STATUS_V11_COMPLETE,
                                       STATUS_V11_DRAINING, STATUS_V11_READ_ONLY,
                                       V11_EXPERIMENT_ID, V11_POLICY_VERSION,
                                       V11_SUPERSEDED_REASON_TR, ExperimentConfig, PolicyBook,
                                       SimClose, SimPosition)

log = logging.getLogger(__name__)

SCHEMA_VERSION = "profitability_store_v1"

#: Tarihsel `pfexp_v1` dosya adları — KORUNUR (değişmez kanıt).
EVENTS_FILE = "profitability_experiment_events.jsonl"
BOOKS_FILE = "profitability_experiment_books.json"
REPORT_FILE = "profitability_experiment.json"
IDENTITY_FILE = "profitability_experiment_identity.json"
LEGACY_EXPERIMENT_ID = "pfexp_v1"

# --- olay türleri --------------------------------------------------------------------------
EV_DECISION = "entry_decision"
EV_OPEN = "open"
EV_MARK = "mark"
EV_CLOSE = "close"
EV_KINDS = (EV_DECISION, EV_OPEN, EV_MARK, EV_CLOSE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def state_file_names(experiment_id: str | None) -> dict[str, str]:
    """Deney kimliğine göre dosya adları. `pfexp_v1` eski adları AYNEN korur."""
    eid = str(experiment_id or LEGACY_EXPERIMENT_ID)
    if eid == LEGACY_EXPERIMENT_ID:
        return {"events": EVENTS_FILE, "books": BOOKS_FILE, "report": REPORT_FILE,
                "identity": IDENTITY_FILE}
    sfx = eid[len("pfexp_"):] if eid.startswith("pfexp_") else eid
    sfx = re.sub(r"[^A-Za-z0-9_]+", "_", sfx).strip("_") or "x"
    return {"events": f"profitability_experiment_{sfx}_events.jsonl",
            "books": f"profitability_experiment_{sfx}_books.json",
            "report": f"profitability_experiment_{sfx}.json",
            "identity": f"profitability_experiment_{sfx}_identity.json"}


def _identity_of(d: dict[str, Any]) -> tuple[str, str, str]:
    return (str(d.get("experiment_id")), str(d.get("policy_version")), str(d.get("config_id")))


class ExperimentStore:
    """Ekle-yalnız olay defteri + atomik kitap anlık görüntüsü (deney kimliğine göre dosyalar)."""

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

    def __init__(self, state_dir: Path | str, *, experiment_id: str | None = None,
                 max_events_per_cycle: int = 500, max_lines: int = 0,
                 archive: Any = None) -> None:
        self.dir = Path(state_dir)
        self.experiment_id = str(experiment_id or LEGACY_EXPERIMENT_ID)
        names = state_file_names(self.experiment_id)
        self.events_path = self.dir / names["events"]
        self.books_path = self.dir / names["books"]
        self.report_path = self.dir / names["report"]
        self.identity_path = self.dir / names["identity"]
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
        #: Kimliği uyuşmayan (yabancı) olaylar — kitaba GİRMEZ, sayılır.
        self.foreign_events = 0
        self._ids: set[str] | None = None

    @property
    def is_legacy(self) -> bool:
        return self.experiment_id == LEGACY_EXPERIMENT_ID

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

    # ------------------------------------------------------------------ salt okunur kimlik
    def read_identity(self) -> dict[str, Any] | None:
        """Kimlik dosyasını YALNIZ OKUR (yoksa None). Hiçbir şey yazmaz, dondurmaz."""
        try:
            if not self.identity_path.exists():
                return None
            d = json.loads(self.identity_path.read_text(encoding="utf-8"))
            if (isinstance(d, dict) and str(d.get("experiment_id")) == self.experiment_id
                    and d.get("evaluation_start_at")):
                return {"experiment_id": self.experiment_id,
                        "evaluation_start_at": str(d["evaluation_start_at"]),
                        "frozen_at": str(d.get("frozen_at") or d["evaluation_start_at"]),
                        "frozen_by_code_sha": d.get("frozen_by_code_sha")}
        except (OSError, ValueError, TypeError):
            return None
        return None

    def read_books_identity(self) -> tuple[str, str, str] | None:
        """Kitap anlık görüntüsünün kimlik üçlüsünü YALNIZ OKUR (yoksa/bozuksa None)."""
        try:
            if not self.books_path.exists():
                return None
            d = json.loads(self.books_path.read_text(encoding="utf-8"))
            return _identity_of(d) if isinstance(d, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ kimlik dondurma
    def freeze_identity(self, *, now=None, code_sha: str | None = None) -> dict[str, Any]:
        """`evaluation_start_at` BİR KEZ dondurulur ve bir daha DEĞİŞMEZ.

        Sıra: (1) kimlik dosyası (aynı `experiment_id`) → (2) kitap anlık görüntüsü (aynı
        `experiment_id`; v1'in kimlik dosyası yoktu) → (3) ŞİMDİ dondur ve kimlik dosyasını
        atomik yaz. Başka bir deneyin başlangıcı ASLA devralınmaz; tarihsel `pfexp_v1`
        deposu için yeni kimlik YAZILMAZ (salt okunur).
        """
        for p, src in ((self.identity_path, "IDENTITY_FILE"), (self.books_path, "BOOKS_SNAPSHOT")):
            try:
                if p.exists():
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if (isinstance(d, dict) and str(d.get("experiment_id")) == self.experiment_id
                            and d.get("evaluation_start_at")):
                        return {"experiment_id": self.experiment_id,
                                "evaluation_start_at": str(d["evaluation_start_at"]),
                                "frozen_at": str(d.get("frozen_at") or d["evaluation_start_at"]),
                                "source": src}
            except (OSError, ValueError, TypeError) as exc:
                log.warning("deney kimliği okunamadı (%s): %s", p.name, exc)
        if self.is_legacy:
            raise ValueError("pfexp_v1 tarihsel deposu SALT OKUNUR: yeni kimlik dondurulamaz")
        n = iso(now or utc_now())
        rec = {"schema_version": SCHEMA_VERSION, "experiment_id": self.experiment_id,
               "evaluation_start_at": n, "frozen_at": n, "frozen_by_code_sha": code_sha,
               "note_tr": ("Deney başlangıcı bu anda BİR KEZ donduruldu; geriye ya da ileriye "
                           "ÇEKİLEMEZ. Bu andan önce açılmış her pozisyon "
                           "PRE_EXPERIMENT_OBSERVATION_ONLY'dir.")}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.identity_path, rec)
            return rec | {"source": "FROZEN_NOW"}
        except Exception as exc:  # noqa: BLE001 — kalıcılık düşerse turu DURDURMAZ, açıkça raporlanır
            self.errors += 1
            log.warning("deney kimliği yazılamadı: %s", exc)
            return rec | {"source": "FROZEN_NOW_UNPERSISTED"}

    # ------------------------------------------------------------------ yazma
    def append(self, event: dict[str, Any]) -> bool:
        """Tek olayı ekler. Aynı `event_id` İKİNCİ KEZ yazılmaz (idempotent).

        Yabancı kimlikli (başka deney/sürüm/config) olay bu deftere YAZILMAZ (fail-closed).
        """
        eid = str((event or {}).get("event_id") or "")
        if not eid:
            self.errors += 1
            return False
        if str((event or {}).get("experiment_id")) != self.experiment_id:
            self.foreign_events += 1
            self.errors += 1
            log.warning("yabancı deney olayı REDDEDİLDİ (%s ≠ %s)",
                        (event or {}).get("experiment_id"), self.experiment_id)
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
                "errors": self.errors, "malformed": self.malformed,
                "foreign_events": self.foreign_events}

    # ------------------------------------------------------------------ replay
    def replay(self, cfg: ExperimentConfig) -> dict[str, PolicyBook]:
        """Olay defterinden kitapları YENİDEN KURAR. Deterministik ve idempotenttir.

        Aynı defter iki kez oynatılırsa AYNI kitap çıkar: her olay `event_id` ile
        tekilleştirilir ve olaylar dosya sırasına göre uygulanır. Kimlik üçlüsü
        (`experiment_id`, `policy_version`, `config_id`) uyuşmayan olay kitaba GİRMEZ.
        """
        books = {p: PolicyBook(p) for p in POLICIES}
        seen: set[str] = set()
        want = cfg.identity_key()
        self.foreign_events = 0
        for e in self.iter_events():
            eid = str(e.get("event_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            if _identity_of(e) != want:
                self.foreign_events += 1
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
        if cfg.experiment_id != self.experiment_id:
            self.errors += 1
            log.warning("kitap yazımı REDDEDİLDİ: depo %s, config %s",
                        self.experiment_id, cfg.experiment_id)
            return {"ok": False, "error": "EXPERIMENT_ID_MISMATCH"}
        payload = {"schema_version": SCHEMA_VERSION, **cfg.identity(),
                   "written_at": iso(utc_now()),
                   "books": {p: b.to_dict() for p, b in sorted(books.items())}}
        blob = json.dumps(payload["books"], sort_keys=True, ensure_ascii=False, default=str)
        payload["checksum_sha256"] = _sha256(blob)
        payload["event_count"] = len(self.known_event_ids())
        payload["foreign_events"] = self.foreign_events
        try:
            atomic_write_json(self.books_path, payload)
            return {"ok": True, "checksum": payload["checksum_sha256"],
                    "path": str(self.books_path)}
        except Exception as exc:  # noqa: BLE001 — anlık görüntü arızası turu DURDURMAZ
            self.errors += 1
            log.warning("deney kitabı yazılamadı: %s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}"}

    @staticmethod
    def _embedded_identity_ok(bd: dict[str, Any], want: tuple[str, str, str]) -> bool:
        """Kitaptaki pozisyon/kapanış kimliği (varsa) config üçlüsüyle uyuşmalı."""
        for v in (bd or {}).values():
            rows = list((v.get("positions") or {}).values()) + list(v.get("closes") or [])
            for r in rows:
                if not isinstance(r, dict) or r.get("experiment_id") is None:
                    continue                      # v1 kayıtları kimlik taşımaz → yalnız üst kimlik
                if _identity_of(r) != want:
                    return False
        return True

    def load_books(self, cfg: ExperimentConfig) -> tuple[dict[str, PolicyBook], dict[str, Any]]:
        """Anlık görüntüyü yükler; kimlik üçlüsü + checksum + gömülü kimlik DOĞRULANIR.

        Herhangi biri uyuşmazsa anlık görüntüye güvenilmez ve kitap defterden replay edilir
        (fail-closed: yanlış kimlikli kitap asla "doğru" sayılmaz).
        """
        meta: dict[str, Any] = {"source": None, "checksum_ok": None, "identity_ok": None}
        want = cfg.identity_key()
        if self.books_path.exists():
            try:
                d = json.loads(self.books_path.read_text(encoding="utf-8"))
                bd = d.get("books") or {}
                blob = json.dumps(bd, sort_keys=True, ensure_ascii=False, default=str)
                ok = (_sha256(blob) == d.get("checksum_sha256"))
                ident_ok = (_identity_of(d) == want) and self._embedded_identity_ok(bd, want)
                meta["checksum_ok"] = ok
                meta["identity_ok"] = ident_ok
                if ok and ident_ok:
                    books = {p: PolicyBook(p) for p in POLICIES}
                    for p, v in bd.items():
                        if p in books:
                            books[p] = PolicyBook.from_dict(v)
                    meta["source"] = "SNAPSHOT"
                    return books, meta
                meta["reason"] = ("CHECKSUM_MISMATCH" if not ok else "IDENTITY_MISMATCH")
            except (OSError, ValueError, TypeError) as exc:
                meta["reason"] = f"UNREADABLE:{type(exc).__name__}"
        meta["source"] = "REPLAY"
        books = self.replay(cfg)
        meta["foreign_events"] = self.foreign_events
        return books, meta

    def stats(self) -> dict[str, Any]:
        n = len(self.known_event_ids())
        return {"schema_version": SCHEMA_VERSION, "experiment_id": self.experiment_id,
                "events": n, "appended": self.appended, "duplicates": self.duplicates,
                "errors": self.errors, "malformed": self.malformed,
                "foreign_events": self.foreign_events,
                "max_lines": self.max_lines,
                "retention_policy": ("NO_ARCHIVE_NO_DELETION" if not self.archive
                                     else "ARCHIVE_FIRST"),
                "silent_deletion": False,
                "events_path": str(self.events_path),
                "books_path": str(self.books_path),
                "identity_path": str(self.identity_path)}

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


# =========================================================================== tarihsel v1

LEGACY_SUPERSEDED_REASON_TR = (
    "pfexp_v1 P1/P4 politikaları A/E kararını kapanmış işlem atıf raporunun `trades` "
    "bölümünden okuyordu; yeni açılan pozisyonun orada satırı OLMADIĞI "
    "için P1/P4 canlı girişte karar anı A/E'yi hiç göremedi ve yapısal olarak ABSTAIN etti. "
    "Deney pratikte yalnız P0/P2/P3'ü ölçtü; geçerli bir beş kollu karşılaştırma DEĞİLDİR. "
    "Kanıt yeniden yazılmadı, geriye dönük doldurulmadı; bu koşu tarihsel/eksik olarak okunur.")


def legacy_v1_summary(state_dir: Path | str) -> dict[str, Any] | None:
    """`pfexp_v1` kanıtının SALT OKUNUR özeti (statü SUPERSEDED_INCOMPLETE_ENTRY_INPUT)."""
    return legacy_summary(state_dir, LEGACY_EXPERIMENT_ID, status=STATUS_SUPERSEDED,
                          policy_version_default=LEGACY_POLICY_VERSION,
                          reason_tr=LEGACY_SUPERSEDED_REASON_TR,
                          coverage_defect_detail_tr=("P1/P4 karar anı A/E girdisinden yoksundu; "
                                                     "ABSTAIN güvenli ama yapısal. Beş politika "
                                                     "arasında karşılaştırılabilirlik YOKTUR."),
                          ae_source=AE_SOURCE_LEGACY)


def legacy_v11_summary(state_dir: Path | str, *, drain_enabled: bool, superseded_by: str | None,
                       admissions_closed_at: str | None, drain_reason: str | None = None
                       ) -> dict[str, Any] | None:
    """`pfexp_v1_1` kanıtının SALT OKUNUR özeti — kabul kapalı (drain) ya da salt okunur.

    Statü: drain açık ve simüle pozisyon kalmadı → COMPLETE; drain açık ve pozisyon var →
    DRAINING; drain kurulamadı → READ_ONLY (F00036 simülasyonları dondurulmuş açık kalır).
    """
    from .profitability_experiment import AE_SOURCE_POINT_IN_TIME
    base = legacy_summary(state_dir, V11_EXPERIMENT_ID, status=STATUS_V11_READ_ONLY,
                          policy_version_default=V11_POLICY_VERSION,
                          reason_tr=V11_SUPERSEDED_REASON_TR,
                          coverage_defect_detail_tr=(
                              "E ailesi birleşik tanı payını (stopsuz spot dahil) futures-only "
                              "bütçeye böldü (entry_v1.0.0 kapsam uyuşmazlığı). Kararlar "
                              "değiştirilmedi; yeni kabul yok."),
                          ae_source=AE_SOURCE_POINT_IN_TIME)
    if base is None:
        return None
    open_sim = base.get("sim_open_by_policy") or {}
    complete = not any(open_sim.values())
    if drain_enabled:
        base["status"] = STATUS_V11_COMPLETE if complete else STATUS_V11_DRAINING
    else:
        base["status"] = STATUS_V11_READ_ONLY
    base["coverage_defect"]["reason_code"] = "E_SCOPE_MISMATCH_COMBINED_VS_FUTURES_BUDGET"
    base["coverage_defect"]["inert_policies"] = []
    base["coverage_defect"]["numerator_scope"] = "COMBINED_SPOT_FUTURES_DIAGNOSTIC"
    base["coverage_defect"]["denominator_scope"] = "FUTURES_STOP_RISK_BUCKET"
    base["admissions_open"] = False
    base["admissions_closed_at"] = admissions_closed_at
    base["superseded_by"] = superseded_by
    base["drain"] = {"enabled": bool(drain_enabled), "complete": complete,
                     "reason": drain_reason, "follow_up_only": True,
                     "open_sim_positions": open_sim}
    return base


def legacy_summary(state_dir: Path | str, experiment_id: str, *, status: str,
                   policy_version_default: str, reason_tr: str,
                   coverage_defect_detail_tr: str, ae_source: str) -> dict[str, Any] | None:
    """Herhangi bir GEÇMİŞ deney sürümünün SALT OKUNUR özeti. Hiçbir dosyaya YAZMAZ.

    Dosya sha256'ları ve KARAR olaylarının ayrı sha256'sı özetle döner: drain'de mark/kapanış
    olayları eklense de karar olaylarının değişmediği tur tur doğrulanabilir.
    """
    st = ExperimentStore(state_dir, experiment_id=experiment_id)
    if not st.events_path.exists() and not st.books_path.exists():
        return None
    books_doc: dict[str, Any] | None = None
    checksum_ok: bool | None = None
    try:
        if st.books_path.exists():
            d = json.loads(st.books_path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                books_doc = d
                blob = json.dumps(d.get("books") or {}, sort_keys=True, ensure_ascii=False,
                                  default=str)
                checksum_ok = (_sha256(blob) == d.get("checksum_sha256"))
    except (OSError, ValueError, TypeError):
        books_doc = None
    events = list(st.iter_events())
    ids = [str(e.get("event_id")) for e in events if e.get("event_id")]
    per_policy: dict[str, dict[str, Any]] = {
        p: {"decisions": {ACCEPT: 0, FILTER: 0, ABSTAIN: 0}, "opens": 0, "marks": 0,
            "closes": 0} for p in POLICIES}
    trades: dict[str, dict[str, Any]] = {}
    identities: dict[str, int] = {}
    for e in events:
        key = "|".join(_identity_of(e))
        identities[key] = identities.get(key, 0) + 1
        pol = str(e.get("policy") or "")
        pp = per_policy.get(pol)
        if pp is None:
            continue
        kind = str(e.get("kind") or "")
        pl = e.get("payload") or {}
        if kind == EV_DECISION:
            dec = str(pl.get("decision") or "")
            if dec in pp["decisions"]:
                pp["decisions"][dec] += 1
            tid = str(pl.get("trade_id") or "")
            if tid:
                trades.setdefault(tid, {"symbol": pl.get("symbol"), "side": pl.get("side"),
                                        "candidate_id": pl.get("candidate_id"),
                                        "as_of": pl.get("as_of"), "policies": {}})
                trades[tid]["policies"][pol] = {"decision": dec,
                                                "reason_codes": list(pl.get("reason_codes") or [])}
        elif kind == EV_OPEN:
            pp["opens"] += 1
        elif kind == EV_MARK:
            pp["marks"] += 1
        elif kind == EV_CLOSE:
            pp["closes"] += 1
    bd = (books_doc or {}).get("books") or {}
    sim_open_by_policy = {p: sorted((bd.get(p) or {}).get("positions") or {}) for p in POLICIES}
    closes = {p: len((bd.get(p) or {}).get("closes") or []) for p in POLICIES}
    inert = sorted({pol for t in trades.values() for pol, d in t["policies"].items()
                    if d["decision"] == ABSTAIN and R_AE_UNKNOWN in d["reason_codes"]})
    dec_events = [e for e in events if str(e.get("kind") or "") == EV_DECISION]
    dec_sha = hashlib.sha256("\n".join(json.dumps(e, sort_keys=True, ensure_ascii=False,
                                                  default=str)
                                       for e in dec_events).encode("utf-8")).hexdigest()
    head = books_doc or {}
    return {
        "status": status,
        "read_only": True,
        "evidence_rewritten": False,
        "backfilled": False,
        "experiment_id": str(head.get("experiment_id") or experiment_id),
        "policy_version": str(head.get("policy_version") or policy_version_default),
        "config_id": head.get("config_id"),
        "code_sha": head.get("code_sha"),
        "evaluation_start_at": head.get("evaluation_start_at"),
        "frozen_at": head.get("frozen_at"),
        "books_written_at": head.get("written_at"),
        "ae_source": ae_source,
        "superseded_reason_tr": reason_tr,
        "coverage_defect": {
            "inert_policies": (inert or ([P1, P4] if experiment_id == LEGACY_EXPERIMENT_ID else [])),
            "reason_code": R_AE_UNKNOWN,
            "detail_tr": coverage_defect_detail_tr,
        },
        "comparable_across_all_policies": False,
        "profitability_conclusion": None,
        "event_count": len(events),
        "n_decision_events": len(dec_events),
        "decision_events_sha256": dec_sha,
        "malformed": st.malformed,
        "duplicate_event_ids": len(ids) - len(set(ids)),
        "identities": identities,
        "books_checksum_ok": checksum_ok,
        "per_policy": per_policy,
        "sim_open_by_policy": sim_open_by_policy,
        "closes": closes,
        "trades": trades,
        "files": {"events": str(st.events_path), "books": str(st.books_path),
                  "report": str(st.report_path)},
        "files_sha256": {"events": _file_sha256(st.events_path),
                         "books": _file_sha256(st.books_path),
                         "report": _file_sha256(st.report_path)},
    }


__all__ = ["SCHEMA_VERSION", "EVENTS_FILE", "BOOKS_FILE", "REPORT_FILE", "IDENTITY_FILE",
           "LEGACY_EXPERIMENT_ID", "LEGACY_SUPERSEDED_REASON_TR", "state_file_names",
           "EV_DECISION", "EV_OPEN", "EV_MARK", "EV_CLOSE", "EV_KINDS", "ExperimentStore",
           "legacy_summary", "legacy_v1_summary", "legacy_v11_summary"]
