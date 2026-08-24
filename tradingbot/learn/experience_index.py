"""Uzun vadeli deneyim indeksi — arşivlenmiş sonuçlar canlı retrieval'da KULLANILABİLİR kalır.

Boşluk: `prepare_pool` yalnız aktif `trade_memory.jsonl` (kesilmez) ve aktif `shadow_book.json`
(`MAX_TRADES` ile sınırlı) okuyordu. Gölge sonuç arşive taşındığı anda — ölçülen tempoda ~61 gün
sonra — karar etkisinden DÜŞÜYORDU. Saklama kayıpsızdı ama retrieval `HOT_ONLY` idi.

Akış:

    mühürlenmiş segment (.jsonl.gz)
    → checksum doğrulaması (fail-closed)
    → segment BİR KEZ normalize edilir
    → kompakt shard (deneyim + vektör)
    → aktif + indekslenmiş geçmiş = hazır havuz
    → aday başına sınırlı benzerlik sorgusu

Değişmezler:

* **Aday başına arşiv TARANMAZ.** Segmentler yalnız YENİ segment göründüğünde ya da indeks
  yeniden kurulurken okunur; kararlı durumda maliyet O(yeni satır)'dır.
* İndeks TÜREV veridir: silinirse kayıpsız arşivden deterministik olarak yeniden kurulur.
* İşlenen her segmentin `segment_id` + `sha256`'sı kaydedilir; aynı segment İKİNCİ KEZ işlenmez.
* Checksum'ı ya da etiket zamanı geçersiz kayıt fail-closed DIŞARIDA kalır.
* Vektörleme `experience.experience_vector` ile yapılır — aktif havuzla AYNI fonksiyon; bir
  kaydın arşive taşınması benzerlik skorunu değiştirmez.
* Bozuk indeks/segment turu ÇÖKERTMEZ: baseline korunur, sağlık `DEGRADED`/`FAILED` olur.
* Karar günlüğü arşivi BURAYA GİRMEZ. Gerçek sonuç için `TradeMemory`, karşı-olgusal sonuç için
  `ShadowBook`/arşivi canonical kaynaktır; karar günlüğü audit/link kanıtıdır. Aksi halde aynı
  outcome üçüncü bir kez sayılırdı.
* Risk/kaldıraç/boyut/stop/TP/emir alanlarına DOKUNMAZ.
"""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now
from .experience import (DEFAULT_SHADOW_FIDELITY, Experience, experience_vector,
                         shadow_experiences)
from .features import FEATURE_VERSION, build_features, feature_names, to_vector

INDEX_SCHEMA_VERSION = "experience_index_v1"
MANIFEST_NAME = "manifest.json"
SHARDS_DIRNAME = "shards"

#: Sağlık durumları — dashboard bunları olduğu gibi gösterir.
HEALTH_EMPTY = "EMPTY"
HEALTH_OK = "OK"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_STALE = "STALE"
HEALTH_FAILED = "FAILED"

#: Retrieval kapsamı — DÜRÜST raporlama: indeks hazır değilse asla genişletilmiş kapsam yazılmaz.
SCOPE_HOT_ONLY = "HOT_ONLY"
SCOPE_HOT_PLUS_INDEXED = "HOT_PLUS_INDEXED_HISTORY"
SCOPE_DEGRADED = "DEGRADED"

#: Kompakt satır anahtarları (indeks boyutu için kısaltıldı).
_K = {"outcome_id": "i", "source": "s", "symbol": "y", "direction": "d", "setup": "u",
      "regime": "g", "r_multiple": "r", "weight": "w", "execution_fidelity": "f",
      "outcome_quality": "q", "provenance": "p", "label_ts_ms": "l", "decision_ts_ms": "t",
      "feature_profile": "fp", "cost_sensitivity": "c", "lesson_codes": "lc"}


def _names_signature(names: list[str]) -> str:
    from ..core import payload_hash
    return payload_hash({"v": FEATURE_VERSION, "n": list(names)})[:16]


def _gzip_bytes(raw: bytes) -> bytes:
    """Deterministik gzip (`mtime=0`) — aynı içerik DAİMA aynı baytlar → yeniden kurulabilirlik."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


class ExperienceIndexStore:
    """Artımlı, yeniden kurulabilir, arşiv-farkındalıklı uzun vadeli deneyim indeksi.

    `root` çağırandan gelir (ör. `cfg.state_path / "experience_index"`). Mutlak yol varsayılmaz.
    """

    def __init__(self, root: Path | str, archive: Any | None, *,
                 names: list[str] | None = None,
                 shadow_weight: float = 0.25,
                 shadow_fidelity: float = DEFAULT_SHADOW_FIDELITY):
        self.root = Path(root)
        self.shards_dir = self.root / SHARDS_DIRNAME
        self.manifest_path = self.root / MANIFEST_NAME
        self.archive = archive
        self.names = list(names or feature_names())
        self.names_sig = _names_signature(self.names)
        self.shadow_weight = float(shadow_weight)
        self.shadow_fidelity = float(shadow_fidelity)
        self.errors = 0
        self.last_error: str | None = None
        # Varsayılan vektör: gölge kayıtlarında ham satır YOKTUR ve aktif havuz da bu
        # vektörü üretir. Aynı olduğunda shard'a YAZILMAZ (indeks boyutu düşer).
        self._default_vec = to_vector(build_features({}), self.names)
        self._default_norm = experience_vector(
            Experience(outcome_id="", source="SHADOW"), {}, self.names)[1]
        self._rows: list[tuple[Experience, list[float], float]] = []
        self._loaded_shards: set[str] = set()

    # ------------------------------------------------------------------ manifest
    def _empty_manifest(self) -> dict[str, Any]:
        now = iso(utc_now())
        return {"schema_version": INDEX_SCHEMA_VERSION, "names_signature": self.names_sig,
                "feature_version": FEATURE_VERSION,
                "shadow_weight": self.shadow_weight, "shadow_fidelity": self.shadow_fidelity,
                "created_at": now, "updated_at": now,
                "health": HEALTH_EMPTY, "last_error": None,
                "last_refresh_at": None, "last_rebuild_at": None,
                "processed": [], "corrupt_segments": [], "skipped_rows": 0,
                "totals": {"segments": 0, "rows": 0, "real": 0, "shadow": 0,
                           "oldest_label_ms": None, "newest_label_ms": None}}

    def manifest(self) -> dict[str, Any]:
        doc = read_json(self.manifest_path, default=None)
        base = self._empty_manifest()
        if not isinstance(doc, dict) or not isinstance(doc.get("processed"), list):
            if self.manifest_path.exists():
                base["health"] = HEALTH_DEGRADED
                base["last_error"] = "INDEX_MANIFEST_UNREADABLE"
            return base
        base.update(doc)
        if not isinstance(base.get("totals"), dict):
            base["totals"] = self._empty_manifest()["totals"]
        return base

    def _write_manifest(self, doc: dict[str, Any]) -> None:
        doc["updated_at"] = iso(utc_now())
        p = doc.get("processed") or []
        t = {"segments": len(p), "rows": 0, "real": 0, "shadow": 0,
             "oldest_label_ms": None, "newest_label_ms": None}
        for s in p:
            if not isinstance(s, dict):
                continue
            t["rows"] += int(s.get("n_rows") or 0)
            t["real"] += int(s.get("n_real") or 0)
            t["shadow"] += int(s.get("n_shadow") or 0)
            for key, dst, better in (("first_label_ms", "oldest_label_ms", min),
                                     ("last_label_ms", "newest_label_ms", max)):
                v = s.get(key)
                if isinstance(v, (int, float)):
                    t[dst] = int(v) if t[dst] is None else int(better(t[dst], int(v)))
        doc["totals"] = t
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.manifest_path, doc, indent=1)

    # ------------------------------------------------------------------ normalize / shard
    def _shard_name(self, seg: dict[str, Any]) -> str:
        seq = seg.get("seq")
        seq_i = int(seq) if isinstance(seq, (int, float)) else 0
        return f"idx-{seq_i:06d}-{seg.get('segment_id')}.jsonl.gz"

    def _encode(self, e: Experience, vec: list[float], norm: float) -> str:
        row: dict[str, Any] = {}
        for attr, key in _K.items():
            val = getattr(e, attr, None)
            if val not in (None, [], ""):
                row[key] = val
        if vec != self._default_vec:
            row["v"] = [round(float(x), 6) for x in vec]
            row["n"] = round(float(norm), 9)
        return json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)

    def _decode(self, raw: str) -> tuple[Experience, list[float], float] | None:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        kwargs: dict[str, Any] = {}
        for attr, key in _K.items():
            if key in row:
                kwargs[attr] = row[key]
        oid = kwargs.get("outcome_id")
        if not oid:
            return None
        # FAIL-CLOSED: etiket zamanı olmayan kayıt no-lookahead filtresini geçemez → indekste
        # tutmanın anlamı yok, `query_pool` zaten eleyecekti.
        lts = kwargs.get("label_ts_ms")
        if not isinstance(lts, (int, float)):
            return None
        kwargs.setdefault("source", "SHADOW")
        try:
            e = Experience(**kwargs)
        except TypeError:
            return None
        vec = row.get("v")
        if isinstance(vec, list) and len(vec) == len(self.names):
            v = [float(x) for x in vec]
            n = float(row.get("n") or 0.0) or (sum(x * x for x in v) ** 0.5 or 1.0)
            return e, v, n
        return e, list(self._default_vec), self._default_norm

    def _normalize_segment(self, rows: list[dict[str, Any]]) -> list[tuple[Experience, list[float], float]]:
        """Ham gölge kayıtları → deneyim + vektör. `as_of` YOK: filtre sorgu anında uygulanır."""
        exps = shadow_experiences(rows, as_of_ms=None, weight=self.shadow_weight,
                                  fidelity=self.shadow_fidelity)
        out: list[tuple[Experience, list[float], float]] = []
        for e in exps:
            if e.label_ts_ms is None:
                continue                       # fail-closed: zamanı bilinmeyen kayıt girmez
            v, n = experience_vector(e, {}, self.names)
            out.append((e, v, n))
        return out

    def _write_shard(self, name: str, rows: list[tuple[Experience, list[float], float]]) -> int:
        payload = _gzip_bytes(
            ("\n".join(self._encode(e, v, n) for e, v, n in rows) + "\n").encode("utf-8")
            if rows else b"")
        self.shards_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.shards_dir / f"{name}.tmp-{os.getpid()}"
        dst = self.shards_dir / name
        try:
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dst)               # atomik: yarım shard görünmez
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            finally:
                pass
            raise
        return len(payload)

    def _read_shard(self, name: str) -> list[tuple[Experience, list[float], float]]:
        path = self.shards_dir / name
        if not path.exists():
            return []
        try:
            with gzip.open(path, "rb") as gz:
                payload = gz.read()
        except (OSError, EOFError, gzip.BadGzipFile):
            self.errors += 1
            self.last_error = f"SHARD_UNREADABLE:{name}"
            return []
        out: list[tuple[Experience, list[float], float]] = []
        for ln in payload.decode("utf-8", errors="replace").splitlines():
            if not ln.strip():
                continue
            dec = self._decode(ln)
            if dec is not None:
                out.append(dec)
        return out

    # ------------------------------------------------------------------ senkronizasyon
    def pending_segments(self) -> list[dict[str, Any]]:
        """Henüz indekslenmemiş arşiv segmentleri (O(manifest), segment AÇMAZ)."""
        if self.archive is None:
            return []
        try:
            done = {str(s.get("segment_id")) for s in (self.manifest().get("processed") or [])
                    if isinstance(s, dict)}
            return [s for s in self.archive.segments() if str(s.get("segment_id")) not in done]
        except Exception:  # noqa: BLE001
            return []

    def refresh(self) -> dict[str, Any]:
        """Yalnız YENİ segmentleri işler. İstisna SIZDIRMAZ; hata sağlığa yazılır.

        Kararlı durumda (yeni segment yok) maliyet: bir manifest okuması. Aday başına DEĞİL,
        tur başına en fazla bir kez çağrılır.
        """
        res = {"new_segments": 0, "new_rows": 0, "corrupt": 0, "health": HEALTH_EMPTY,
               "error": None, "rebuilt": False}
        if self.archive is None:
            res["health"] = HEALTH_EMPTY
            return res
        try:
            doc = self.manifest()
            # Özellik şeması ya da gölge ağırlıkları değiştiyse indeks GEÇERSİZDİR —
            # normalize edilmiş satırlar bu parametrelere bağlıdır → arşivden yeniden kurulur.
            stale = (str(doc.get("names_signature") or "") != self.names_sig
                     or float(doc.get("shadow_weight", self.shadow_weight)) != self.shadow_weight
                     or float(doc.get("shadow_fidelity", self.shadow_fidelity)) != self.shadow_fidelity)
            if doc.get("processed") and stale:
                self.rebuild()
                res["rebuilt"] = True
                doc = self.manifest()
            processed = [s for s in (doc.get("processed") or []) if isinstance(s, dict)]
            done = {str(s.get("segment_id")) for s in processed}
            corrupt = list(doc.get("corrupt_segments") or [])
            changed = False
            for seg in self.archive.segments():
                sid = str(seg.get("segment_id"))
                if sid in done:
                    continue                   # AYNI SEGMENT İKİNCİ KEZ İŞLENMEZ
                rows = self.archive.read_segment(seg)
                if rows is None:               # checksum düştü → fail-closed
                    if sid not in corrupt:
                        corrupt.append(sid)
                    res["corrupt"] += 1
                    changed = True
                    continue
                norm = self._normalize_segment(rows)
                name = self._shard_name(seg)
                nbytes = self._write_shard(name, norm)
                labels = [e.label_ts_ms for e, _, _ in norm if e.label_ts_ms is not None]
                processed.append({
                    "segment_id": sid, "sha256": seg.get("sha256"),
                    "block_sha256": seg.get("block_sha256"), "seq": seg.get("seq"),
                    "shard": name, "n_rows": len(norm),
                    "n_real": sum(1 for e, _, _ in norm if e.source == "REAL_PAPER"),
                    "n_shadow": sum(1 for e, _, _ in norm if e.source != "REAL_PAPER"),
                    "n_source_rows": len(rows), "n_skipped": len(rows) - len(norm),
                    "first_label_ms": min(labels) if labels else None,
                    "last_label_ms": max(labels) if labels else None,
                    "bytes": nbytes, "indexed_at": iso(utc_now())})
                done.add(sid)
                res["new_segments"] += 1
                res["new_rows"] += len(norm)
                changed = True
            if changed:
                processed.sort(key=lambda s: (int(s.get("seq") or 0), str(s.get("segment_id"))))
                doc["processed"] = processed
                doc["corrupt_segments"] = corrupt
                doc["names_signature"] = self.names_sig
                doc["shadow_weight"] = self.shadow_weight
                doc["shadow_fidelity"] = self.shadow_fidelity
                doc["health"] = HEALTH_DEGRADED if corrupt else HEALTH_OK
                doc["last_error"] = (f"CORRUPT_SEGMENTS:{len(corrupt)}" if corrupt else None)
                doc["last_refresh_at"] = iso(utc_now())
                self._write_manifest(doc)
            res["health"] = doc.get("health") or (HEALTH_OK if processed else HEALTH_EMPTY)
        except Exception as exc:  # noqa: BLE001 — indeks arızası turu ÇÖKERTMEZ
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            res["health"] = HEALTH_FAILED
            res["error"] = self.last_error
        return res

    def rebuild(self) -> dict[str, Any]:
        """İndeksi sıfırlayıp KAYIPSIZ arşivden deterministik olarak yeniden kurar."""
        import shutil
        try:
            shutil.rmtree(self.shards_dir, ignore_errors=True)
            self.manifest_path.unlink(missing_ok=True)
        except OSError:
            self.errors += 1
        self._rows = []
        self._loaded_shards = set()
        doc = self._empty_manifest()
        doc["last_rebuild_at"] = iso(utc_now())
        self._write_manifest(doc)
        return self.refresh()

    def rows(self) -> list[tuple[Experience, list[float], float]]:
        """İndekslenmiş geçmiş — belleğe ARTIMLI yüklenir (yalnız yeni shard okunur)."""
        try:
            for s in self.manifest().get("processed") or []:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("shard") or "")
                if not name or name in self._loaded_shards:
                    continue
                self._rows.extend(self._read_shard(name))
                self._loaded_shards.add(name)
        except Exception as exc:  # noqa: BLE001 — bozuk indeks baseline'ı bozamaz
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        return self._rows

    def signature(self) -> tuple:
        """Hazır havuz önbelleği için ucuz imza — manifest (mtime, size) + satır sayısı."""
        try:
            st = self.manifest_path.stat()
            return (st.st_mtime_ns, st.st_size, len(self._rows))
        except OSError:
            return (0, 0, len(self._rows))

    # ------------------------------------------------------------------ raporlama
    def stats(self) -> dict[str, Any]:
        """O(1) özet — shard AÇMAZ. `retrieval_scope` DÜRÜSTTÜR: hazır değilse HOT_ONLY."""
        doc = self.manifest()
        t = doc.get("totals") or {}
        n_seg = int(t.get("segments") or 0)
        n_rows = int(t.get("rows") or 0)
        corrupt = list(doc.get("corrupt_segments") or [])
        health = str(doc.get("health") or HEALTH_EMPTY)
        if self.last_error and health == HEALTH_OK:
            health = HEALTH_DEGRADED
        lag = len(self.pending_segments())
        if lag > 0 and health == HEALTH_OK:
            health = HEALTH_STALE
        if health in (HEALTH_OK, HEALTH_STALE) and n_rows > 0:
            scope = SCOPE_HOT_PLUS_INDEXED
        elif health in (HEALTH_DEGRADED, HEALTH_FAILED):
            scope = SCOPE_DEGRADED
        else:
            scope = SCOPE_HOT_ONLY
        return {"schema_version": INDEX_SCHEMA_VERSION, "root": str(self.root),
                "indexed_experiences": n_rows,
                "indexed_real": int(t.get("real") or 0),
                "indexed_shadow": int(t.get("shadow") or 0),
                "processed_segments": n_seg,
                "corrupt_segments": len(corrupt),
                "skipped_rows": sum(int(s.get("n_skipped") or 0)
                                    for s in (doc.get("processed") or [])
                                    if isinstance(s, dict)),
                "oldest_label_ms": t.get("oldest_label_ms"),
                "newest_label_ms": t.get("newest_label_ms"),
                "index_lag_segments": lag,
                "last_refresh_at": doc.get("last_refresh_at"),
                "last_rebuild_at": doc.get("last_rebuild_at"),
                "index_health": health, "last_index_error": self.last_error or doc.get("last_error"),
                "retrieval_scope": scope,
                "no_lookahead": "AS_OF_ENFORCED_FAIL_CLOSED",
                "rebuildable_from_archive": True,
                "names_signature": doc.get("names_signature") or self.names_sig}


__all__ = ["ExperienceIndexStore", "HEALTH_DEGRADED", "HEALTH_EMPTY", "HEALTH_FAILED",
           "HEALTH_OK", "HEALTH_STALE", "INDEX_SCHEMA_VERSION", "SCOPE_DEGRADED",
           "SCOPE_HOT_ONLY", "SCOPE_HOT_PLUS_INDEXED"]
