"""Kayıpsız segment arşivi — sıcak dosya SINIRLI kalır, hiçbir öğrenme kaydı SİLİNMEZ.

Neden: `DecisionJournal.prune()` satır sınırını aşan en eski kayıtları DOĞRUDAN atıyordu ve
`ShadowBook.save()` `MAX_TRADES` üstündeki en eski gölge işlemleri sessizce düşürüyordu.
Ölçüldü: 38 aday/tur × 96 tur/gün = ~3.6k kayıt/gün → 20.000 satırlık sıcak dosya ~5.5 günde
dolar ve o andan itibaren HER tur en eski kayıtlar kalıcı olarak yok olurdu.

Sözleşme:

    sıcak günlük
    → atomik mühürlenmiş segment (.jsonl.gz)
    → checksum + manifest
    → yalnız BUNDAN SONRA sıcak günlük budanır

Değişmezler:

* Arşiv yazımı başarısızsa ya da checksum tutmazsa sıcak dosya **budanmaz** (fail-closed).
* Varsayılan saklama SINIRSIZ: `max_segments=0` → hiçbir segment silinmez. Silme yalnız
  çağıran açıkça pozitif bir sınır verirse mümkündür (`retention_policy` manifestte görünür).
* `segment_id` ve dosya adı TAMAMEN İÇERİKTEN türer → aynı blok ikinci kez mühürlenirse aynı
  dosya adı ve aynı sha256 çıkar; çift kayıt oluşmaz (idempotent retry).
* Çökme sonrası `pending_trim` ile devam edilir: manifest işlendi ama budama yapılmadıysa
  yeniden başlatmada budama tamamlanır; budama zaten yapılmışsa kayıt DOĞRULANIP temizlenir.
* gzip `mtime=0` ile deterministiktir; aynı içerik → aynı bayt → aynı sha256.
* Sıcak döngü arşivi TARAMAZ; `stats()` yalnız manifestten okur (O(1)).
* Yol çağırandan gelir (state/data kökü); modül içinde mutlak yol yoktur.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from ..core import atomic_write_json, iso, read_json, stable_id, utc_now

ARCHIVE_SCHEMA_VERSION = "journal_archive_v1"
MANIFEST_NAME = "manifest.json"
SEGMENTS_DIRNAME = "segments"

#: Arşiv sağlık durumları — dashboard bunları olduğu gibi gösterir.
HEALTH_OK = "OK"
HEALTH_EMPTY = "EMPTY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_FAILED = "ARCHIVE_FAILED"

#: Varsayılan saklama: silme YOK.
RETENTION_UNLIMITED = "UNLIMITED_NO_DELETION"

_SEG_RE = re.compile(r"^seg-(\d{8}T\d{6}Z|00000000T000000Z)-([0-9a-f]{16})\.jsonl\.gz$")
_TS_SAFE = re.compile(r"[^0-9A-Za-z]")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gzip_bytes(raw: bytes) -> bytes:
    """Deterministik gzip — `mtime=0`, sabit seviye. Aynı içerik DAİMA aynı baytları verir."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


def _ts_token(ts: str | None) -> str:
    """ISO zaman → dosya adı için sıralanabilir belirteç. Bilinmiyorsa sabit sıfır belirteci."""
    if not ts:
        return "00000000T000000Z"
    cleaned = _TS_SAFE.sub("", str(ts))[:14]
    if len(cleaned) < 14 or not cleaned.isdigit():
        return "00000000T000000Z"
    return f"{cleaned[:8]}T{cleaned[8:14]}Z"


def _identity(row: dict[str, Any]) -> str | None:
    """Kayıt kimliği — karar için `decision_id`, sonuç bağlantısı için `trade_id`."""
    for key in ("decision_id", "trade_id", "id"):
        v = row.get(key)
        if v:
            return str(v)
    return None


def _row_ts(row: dict[str, Any]) -> str | None:
    for key in ("decision_ts", "outcome_ts", "created_at", "labeled_at", "recorded_at"):
        v = row.get(key)
        if v:
            return str(v)
    return None


def scan_block(lines: list[str]) -> dict[str, Any]:
    """Ham satır bloğunun özeti — bozuk satırlar sayılır ama bloğu REDDETMEZ (kayıp yasak)."""
    n_dec = n_out = n_bad = 0
    first_ts = last_ts = None
    first_id = last_id = None
    ids: list[str] = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except (json.JSONDecodeError, TypeError):
            n_bad += 1
            continue
        if not isinstance(row, dict):
            n_bad += 1
            continue
        kind = str(row.get("kind") or "")
        if kind == "outcome_link":
            n_out += 1
        else:
            n_dec += 1
        ts = _row_ts(row)
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
        ident = _identity(row)
        if ident:
            ids.append(ident)
    if ids:
        first_id, last_id = ids[0], ids[-1]
    return {"n_records": len(lines), "n_decisions": n_dec, "n_outcomes": n_out,
            "n_unparseable": n_bad, "first_ts": first_ts, "last_ts": last_ts,
            "first_identity": first_id, "last_identity": last_id}


class ArchiveError(Exception):
    """Arşiv yazımı/doğrulaması başarısız — çağıran sıcak dosyayı BUDAMAMALIDIR."""


class SegmentArchive:
    """Sıkıştırılmış, checksum'lı, değişmez segment arşivi + atomik manifest.

    `root` çağırandan gelir (ör. `cfg.state_path / "decision_archive"`); modül hiçbir mutlak
    yol varsaymaz. Segmentler `root/segments/` altındadır, manifest `root/manifest.json`.
    """

    def __init__(self, root: Path | str, *, stream_id: str,
                 record_schema_version: str | None = None,
                 code_sha: str | None = None, max_segments: int = 0):
        self.root = Path(root)
        self.segments_dir = self.root / SEGMENTS_DIRNAME
        self.manifest_path = self.root / MANIFEST_NAME
        self.stream_id = str(stream_id)
        self.record_schema_version = record_schema_version
        self.code_sha = code_sha
        #: 0 (varsayılan) → SINIRSIZ saklama, hiçbir segment silinmez.
        self.max_segments = max(0, int(max_segments))
        self.errors = 0

    # ------------------------------------------------------------------ manifest
    def _empty_manifest(self) -> dict[str, Any]:
        now = iso(utc_now())
        return {"schema_version": ARCHIVE_SCHEMA_VERSION, "stream_id": self.stream_id,
                "record_schema_version": self.record_schema_version,
                "created_at": now, "updated_at": now,
                "health": HEALTH_EMPTY, "last_error": None, "last_rotation_at": None,
                "retention_policy": (RETENTION_UNLIMITED if self.max_segments == 0
                                     else f"KEEP_LAST_{self.max_segments}"),
                "segments": [], "pending_trim": None, "deleted_segments": 0,
                "totals": {"segments": 0, "records": 0, "decisions": 0, "outcomes": 0,
                           "unparseable": 0, "bytes_compressed": 0, "bytes_raw": 0,
                           "first_ts": None, "last_ts": None}}

    def manifest(self) -> dict[str, Any]:
        """Manifest — bozuk/eksik dosyada BOŞ manifest döner, ASLA istisna sızdırmaz."""
        doc = read_json(self.manifest_path, default=None)
        if not isinstance(doc, dict) or not isinstance(doc.get("segments"), list):
            base = self._empty_manifest()
            if self.manifest_path.exists():
                base["health"] = HEALTH_DEGRADED
                base["last_error"] = "MANIFEST_UNREADABLE"
            return base
        base = self._empty_manifest()
        base.update(doc)
        if not isinstance(base.get("totals"), dict):
            base["totals"] = self._empty_manifest()["totals"]
        return base

    def _recompute_totals(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        t = {"segments": len(segments), "records": 0, "decisions": 0, "outcomes": 0,
             "unparseable": 0, "bytes_compressed": 0, "bytes_raw": 0,
             "first_ts": None, "last_ts": None}
        for s in segments:
            for src, dst in (("n_records", "records"), ("n_decisions", "decisions"),
                             ("n_outcomes", "outcomes"), ("n_unparseable", "unparseable"),
                             ("bytes_compressed", "bytes_compressed"),
                             ("bytes_raw", "bytes_raw")):
                v = s.get(src)
                if isinstance(v, (int, float)):
                    t[dst] += int(v)
            for key, dst in (("first_ts", "first_ts"), ("last_ts", "last_ts")):
                v = s.get(key)
                if not v:
                    continue
                if dst == "first_ts" and (t["first_ts"] is None or v < t["first_ts"]):
                    t["first_ts"] = v
                if dst == "last_ts" and (t["last_ts"] is None or v > t["last_ts"]):
                    t["last_ts"] = v
        return t

    @staticmethod
    def _next_seq(segments: list[dict[str, Any]]) -> int:
        seqs = [int(s["seq"]) for s in segments if isinstance(s.get("seq"), (int, float))]
        return (max(seqs) + 1) if seqs else 1

    @staticmethod
    def _ordered(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Segmentleri ARŞİVLEME sırasına göre dizer (`seq`); eski kayıtlarda `first_ts` yedeği."""
        return sorted(segments, key=lambda s: (
            int(s["seq"]) if isinstance(s.get("seq"), (int, float)) else 1 << 30,
            str(s.get("first_ts") or ""), str(s.get("segment_id") or "")))

    def _write_manifest(self, doc: dict[str, Any]) -> None:
        doc["updated_at"] = iso(utc_now())
        doc["retention_policy"] = (RETENTION_UNLIMITED if self.max_segments == 0
                                   else f"KEEP_LAST_{self.max_segments}")
        doc["totals"] = self._recompute_totals(doc.get("segments") or [])
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.manifest_path, doc, indent=1)

    # ------------------------------------------------------------------ mühürleme
    def _segment_path(self, meta: dict[str, Any]) -> Path:
        return self.segments_dir / str(meta["file"])

    def seal(self, lines: list[str]) -> dict[str, Any]:
        """Ham satır bloğunu değişmez segmente mühürler. Başarısızlıkta `ArchiveError`.

        Adımlar: geçici dosyaya yaz → flush+fsync → checksum ve tam açılım doğrula →
        atomik rename. Manifest BU AŞAMADA yazılmaz (bkz. `commit`).
        """
        clean = [ln for ln in lines if ln.strip()]
        if not clean:
            raise ArchiveError("boş blok mühürlenemez")
        raw = ("\n".join(clean) + "\n").encode("utf-8")
        block_sha = _sha256_bytes(raw)
        info = scan_block(clean)
        segment_id = stable_id("seg", self.stream_id, block_sha, len(clean))
        fname = f"seg-{_ts_token(info['first_ts'])}-{segment_id}.jsonl.gz"
        meta = {"segment_id": segment_id, "schema_version": ARCHIVE_SCHEMA_VERSION,
                "record_schema_version": self.record_schema_version,
                "file": fname, "source_stream_id": self.stream_id,
                "first_decision_ts": info["first_ts"], "last_decision_ts": info["last_ts"],
                "first_identity": info["first_identity"], "last_identity": info["last_identity"],
                "n_records": info["n_records"], "n_decisions": info["n_decisions"],
                "n_outcomes": info["n_outcomes"], "n_unparseable": info["n_unparseable"],
                "first_ts": info["first_ts"], "last_ts": info["last_ts"],
                "block_sha256": block_sha, "bytes_raw": len(raw),
                "created_at": iso(utc_now()), "code_sha": self.code_sha,
                "status": "SEALED"}
        dst = self.segments_dir / fname

        # İdempotent: aynı blok daha önce mühürlenmişse yeniden yazma, doğrula ve dön.
        if dst.exists():
            try:
                existing = _sha256_file(dst)
                if self._verify_payload(dst, block_sha):
                    meta["sha256"] = existing
                    meta["bytes_compressed"] = dst.stat().st_size
                    return meta
            except OSError as exc:
                raise ArchiveError(f"mevcut segment okunamadı: {dst}: {exc}") from exc
            raise ArchiveError(f"segment çakışması, içerik uyuşmuyor: {dst}")

        payload = _gzip_bytes(raw)
        sha = _sha256_bytes(payload)
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.segments_dir / f"{fname}.tmp-{os.getpid()}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            if _sha256_file(tmp) != sha:
                raise ArchiveError("geçici segment checksum'ı uyuşmadı")
            if not self._verify_payload(tmp, block_sha):
                raise ArchiveError("geçici segment açılım doğrulaması başarısız")
            os.replace(tmp, dst)
        except (OSError, ArchiveError) as exc:
            self.errors += 1
            try:
                tmp.unlink(missing_ok=True)
            finally:
                pass
            raise ArchiveError(f"segment yazılamadı: {fname}: {exc}") from exc
        meta["sha256"] = sha
        meta["bytes_compressed"] = len(payload)
        return meta

    @staticmethod
    def _verify_payload(path: Path, block_sha: str) -> bool:
        """Sıkıştırılmış dosyayı TAMAMEN açar ve ham blok sha256'sıyla karşılaştırır."""
        try:
            with gzip.open(path, "rb") as gz:
                return _sha256_bytes(gz.read()) == block_sha
        except (OSError, EOFError, gzip.BadGzipFile):
            return False

    def commit(self, meta: dict[str, Any], *, pending_trim: dict[str, Any] | None = None) -> None:
        """Segmenti manifeste ATOMİK ekler. Aynı `segment_id` ikinci kez eklenmez."""
        doc = self.manifest()
        segs = [s for s in (doc.get("segments") or []) if isinstance(s, dict)]
        if not any(str(s.get("segment_id")) == str(meta.get("segment_id")) for s in segs):
            # `seq` = ARŞİVLEME sırası. Kayıt zamanına göre sıralamak yanlış olurdu: günlük
            # sıralı bir AKIŞTIR ve zaman damgaları her zaman monoton değildir.
            meta = {**meta, "seq": self._next_seq(segs)}
            segs.append(meta)
        doc["segments"] = self._ordered(segs)
        doc["pending_trim"] = pending_trim
        doc["last_rotation_at"] = iso(utc_now())
        doc["health"] = HEALTH_OK
        doc["last_error"] = None
        self._write_manifest(doc)
        self._enforce_retention()

    def _enforce_retention(self) -> None:
        """Varsayılanda HİÇBİR ŞEY YAPMAZ. Yalnız açıkça pozitif sınır verilirse siler."""
        if self.max_segments <= 0:
            return
        doc = self.manifest()
        segs = [s for s in (doc.get("segments") or []) if isinstance(s, dict)]
        if len(segs) <= self.max_segments:
            return
        drop, keep = segs[:-self.max_segments], segs[-self.max_segments:]
        for s in drop:
            try:
                self._segment_path(s).unlink(missing_ok=True)
            except OSError:
                self.errors += 1
        doc["segments"] = keep
        doc["deleted_segments"] = int(doc.get("deleted_segments") or 0) + len(drop)
        self._write_manifest(doc)

    def pending_trim(self) -> dict[str, Any] | None:
        pt = self.manifest().get("pending_trim")
        return pt if isinstance(pt, dict) and pt.get("block_sha256") else None

    def clear_pending_trim(self) -> None:
        doc = self.manifest()
        if doc.get("pending_trim") is None:
            return
        doc["pending_trim"] = None
        self._write_manifest(doc)

    def segment_for(self, segment_id: str) -> dict[str, Any] | None:
        for s in self.manifest().get("segments") or []:
            if isinstance(s, dict) and str(s.get("segment_id")) == str(segment_id):
                return s
        return None

    # ------------------------------------------------------------------ kurtarma
    def recover(self) -> dict[str, Any]:
        """Yarım kalmış işleri toparlar: yetim `.tmp-*` temizliği + manifeste düşmemiş segmentler.

        Çökme senaryosu: segment atomik olarak yerine kondu ama manifest yazılamadı. Segment
        diskte DURUYOR; burada checksum'ı doğrulanıp manifeste geri alınır — kayıp yok.
        """
        out = {"orphan_tmp_removed": 0, "adopted": 0, "corrupt": 0}
        if not self.segments_dir.exists():
            return out
        for tmp in self.segments_dir.glob("*.tmp-*"):
            try:
                tmp.unlink()
                out["orphan_tmp_removed"] += 1
            except OSError:
                self.errors += 1
        doc = self.manifest()
        segs = [s for s in (doc.get("segments") or []) if isinstance(s, dict)]
        known = {str(s.get("file")) for s in segs}
        changed = False
        for path in sorted(self.segments_dir.glob("seg-*.jsonl.gz")):
            if path.name in known:
                continue
            m = _SEG_RE.match(path.name)
            if not m:
                continue
            try:
                with gzip.open(path, "rb") as gz:
                    raw = gz.read()
            except (OSError, EOFError, gzip.BadGzipFile):
                out["corrupt"] += 1
                doc["health"] = HEALTH_DEGRADED
                doc["last_error"] = f"UNREADABLE_SEGMENT:{path.name}"
                changed = True
                continue
            lines = raw.decode("utf-8", errors="replace").splitlines()
            info = scan_block([ln for ln in lines if ln.strip()])
            block_sha = _sha256_bytes(raw)
            meta = {"segment_id": m.group(2), "schema_version": ARCHIVE_SCHEMA_VERSION,
                    "record_schema_version": self.record_schema_version,
                    "file": path.name, "source_stream_id": self.stream_id,
                    "first_decision_ts": info["first_ts"], "last_decision_ts": info["last_ts"],
                    "first_identity": info["first_identity"],
                    "last_identity": info["last_identity"],
                    "n_records": info["n_records"], "n_decisions": info["n_decisions"],
                    "n_outcomes": info["n_outcomes"], "n_unparseable": info["n_unparseable"],
                    "first_ts": info["first_ts"], "last_ts": info["last_ts"],
                    "block_sha256": block_sha, "bytes_raw": len(raw),
                    "sha256": _sha256_file(path), "bytes_compressed": path.stat().st_size,
                    "created_at": iso(utc_now()), "code_sha": self.code_sha,
                    "seq": self._next_seq(segs), "status": "ADOPTED_AFTER_CRASH"}
            segs.append(meta)
            out["adopted"] += 1
            changed = True
        if changed:
            doc["segments"] = self._ordered(segs)
            self._write_manifest(doc)
        return out

    # ------------------------------------------------------------------ doğrulama / okuma
    def verify(self) -> dict[str, Any]:
        """Her segmentin sha256'sını ve açılımını doğrular. Bozuk segment ADI ile raporlanır."""
        doc = self.manifest()
        ok: list[str] = []
        bad: list[str] = []
        missing: list[str] = []
        for s in doc.get("segments") or []:
            if not isinstance(s, dict):
                continue
            path = self._segment_path(s)
            if not path.exists():
                missing.append(str(s.get("segment_id")))
                continue
            try:
                same = _sha256_file(path) == str(s.get("sha256") or "")
            except OSError:
                same = False
            if same and self._verify_payload(path, str(s.get("block_sha256") or "")):
                ok.append(str(s.get("segment_id")))
            else:
                bad.append(str(s.get("segment_id")))
        health = HEALTH_OK if not (bad or missing) else HEALTH_DEGRADED
        if not (ok or bad or missing):
            health = HEALTH_EMPTY
        return {"health": health, "ok": ok, "corrupt": bad, "missing": missing,
                "n_segments": len(ok) + len(bad) + len(missing)}

    def iter_rows(self, *, verify_checksums: bool = True) -> Iterator[dict[str, Any]]:
        """Arşivlenmiş kayıtları zaman sırasıyla verir — OFFLINE/rapor yolu içindir.

        Checksum'ı tutmayan segment ÖĞRENMEYE KATILMAZ (sessizce atlanmaz; `verify()` ve
        manifest sağlığı üzerinden görünür kalır).
        """
        for s in self.manifest().get("segments") or []:
            if not isinstance(s, dict):
                continue
            path = self._segment_path(s)
            if not path.exists():
                continue
            if verify_checksums:
                try:
                    if _sha256_file(path) != str(s.get("sha256") or ""):
                        continue
                except OSError:
                    continue
            try:
                with gzip.open(path, "rb") as gz:
                    payload = gz.read()
            except (OSError, EOFError, gzip.BadGzipFile):
                continue
            for ln in payload.decode("utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row

    def stats(self) -> dict[str, Any]:
        """Manifestten O(1) özet — sıcak döngü ve dashboard için. Segment AÇMAZ."""
        doc = self.manifest()
        t = doc.get("totals") or {}
        return {"schema_version": ARCHIVE_SCHEMA_VERSION,
                "root": str(self.root), "stream_id": self.stream_id,
                "n_segments": int(t.get("segments") or 0),
                "n_archived_records": int(t.get("records") or 0),
                "n_archived_decisions": int(t.get("decisions") or 0),
                "n_archived_outcomes": int(t.get("outcomes") or 0),
                "bytes_compressed": int(t.get("bytes_compressed") or 0),
                "bytes_raw": int(t.get("bytes_raw") or 0),
                "oldest_ts": t.get("first_ts"), "newest_ts": t.get("last_ts"),
                "last_rotation_at": doc.get("last_rotation_at"),
                "health": doc.get("health") or HEALTH_EMPTY,
                "last_error": doc.get("last_error"),
                "retention_policy": doc.get("retention_policy") or RETENTION_UNLIMITED,
                "deleted_segments": int(doc.get("deleted_segments") or 0),
                "pending_trim": bool(doc.get("pending_trim"))}


__all__ = ["ARCHIVE_SCHEMA_VERSION", "ArchiveError", "HEALTH_DEGRADED", "HEALTH_EMPTY",
           "HEALTH_FAILED", "HEALTH_OK", "MANIFEST_NAME", "RETENTION_UNLIMITED",
           "SEGMENTS_DIRNAME", "SegmentArchive", "scan_block"]
