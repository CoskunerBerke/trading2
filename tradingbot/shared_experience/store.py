"""ORTAK DENEYİM KATMANI v1 — TEK HAFIZA deposu (2026-09-29). Bütün defterlerin satırları tek akışa yazılır.

`ExperienceStore`, `learn.decision_journal.DecisionJournal`in alt sınıfıdır ve onun KAYIPSIZ döngü sırasını kullanır:
sıcak dosya `root/experience.jsonl` (≤ `hot_max_lines` = 5000 satır) taşınca taşan baş blok ÖNCE `root/archive`e
(`learn.journal_archive.SegmentArchive`: gzip + sha256 + manifest, `pending_trim` ile çökme kurtarma) mühürlenir, ANCAK
ondan sonra sıcak dosyadan çıkarılır; arşiv başarısızsa HİÇBİR satır silinmez.

DÖNGÜ HİSTEREZİSİ (2026-09-29, inceleme bulgusu): miras döngü dosyayı TAM sınıra budadığı için dosya dolunca her adım
(her tur) 10–15 satırlık bir segment mühürlüyor, manifesti iki kez yazıyor, segment klasörünü tarıyor ve 13 MB sıcak dosyayı
yeniden yazıp ayrıştırıyordu (günde ~240 segment; adım maliyeti segment sayısıyla doğrusal büyüyüp ~4 ayda devre kesiciyi
tetikliyordu). Artık döngü yalnız sınır AŞILINCA çalışır ve dosyayı `hot_max_lines × ROTATE_KEEP_FRACTION` satıra budar
(segment ≥ 2500 satır, günde ~1 döngü); sınırın altındaki çağrı G/Ç YAPMAZ. Kurtarma (`recover` + bekleyen budama) süreç
başına bir kez (ilk çağrı; toplayıcı kurulurken çağırır) ve arşiv hatasından sonra yeniden yapılır; arşiv bayt önbelleği
yalnız döngüden sonra tazelenir; idempotency kümesinden yalnız arşive giden bloğun kimlikleri düşer.

Bu sınıfın eklediği sözleşme (2026-09-29, SPEC_V1 §3.1/§8, KARARLAR 10):

* `append_rows(rows)` TOPLU yazar: satır başına doğrulama (şema `shared_experience_row_v1`, tür `xp_*` — ASLA
  `decision`/`outcome_link`, 16 hex `row_id` = `decision_id`, `app_mode == "PAPER"`, bilinen defter) ve
  `json.dumps(allow_nan=False)` (NaN/sonsuz → RED ve sayılır); ardından TEK `open` + `flush` + `fsync`, yol bazlı ortak
  kilit altında. `row_id` ile İDEMPOTENT: aynı satır ikinci kez yazılmaz (süreç içi `_seen`; yeniden başlatmada
  `load_seen()` sıcak dosyadan doldurur; arşivdeki eski kopyalar okuma yolunda `iter_all_rows` ile tekilleşir).
* Yarım son satır (çökme) yeni satırları BOZMAZ: dosya `\\n` ile bitmiyorsa önce ayraç yazılır; yarım satır kayıpsız kalır
  (okuyucu atlar, arşiv `n_unparseable` sayar).
* G/Ç hatası YARIM TOPLU YAZIM BIRAKMAZ (2026-09-29, inceleme bulgusu): yazım/fsync hatasında dosya yazımdan önceki boyuna
  geri kesilir (`rollback`); eskiden baytlar diskte kalıyor, yeniden deneme aynı `row_id` ile YENİ içerik yazıyor ve okuyucu
  eski kopyayı tutuyordu. Geri kesme de başarısızsa sayılır (`rollback_failed`) ve okuyucu eşit revizyonda SONUNCUYU alır.
* Disk tavanı `max_total_mb` (sıcak dosya + arşiv manifestindeki sıkıştırılmış bayt) — bozulma SIRASI:
  %90 → WARN (hepsi yazılır); %100 → THINNING: YALNIZ zaten ≥ 10.000 etiketli karşı-olgusal satırı olan defterlerin
  rev-0 karşı-olgusal satırları 1/2 seyreltilir (anahtar paritesiyle deterministik; kalanlar `thinned: true`);
  %110 → DEGRADED: bütün yazımlar durur (sayılır). HİÇBİR ŞEY SİLİNMEZ.
* Depo yalnız KENDİ klasörüne yazar; defter/pozisyon/plan/öğrenici/karar dosyalarına DOKUNMAZ.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core import stable_id
from ..learn.decision_journal import DecisionJournal
from ..learn.journal_archive import ArchiveError, SegmentArchive
from .rows import APP_MODE_PAPER, BOOKS, KIND_CF, KINDS, LABELLED, ROW_SCHEMA

HOT_FILE = "experience.jsonl"
ARCHIVE_DIR = "archive"
STREAM_ID = "shared_experience"
DEFAULT_HOT_MAX_LINES = 5000
DEFAULT_MAX_TOTAL_MB = 1024.0
#: Döngü histerezisi (2026-09-29): sınır aşılınca sıcak dosya bu orana budanır (5000 → 2500; segment ≥ 2500 satır).
ROTATE_KEEP_FRACTION = 0.5

#: Disk baskısı eşikleri (tavanın oranı) ve durumları (2026-09-29).
WARN_FRACTION, THIN_FRACTION, DEGRADE_FRACTION = 0.90, 1.00, 1.10
STATE_OK, STATE_WARN, STATE_THINNING, STATE_DEGRADED = "OK", "WARN", "THINNING", "DEGRADED"
PRESSURE_STATES = (STATE_OK, STATE_WARN, STATE_THINNING, STATE_DEGRADED)
#: Seyreltmeye aday defter: bu kadar ETİKETLİ karşı-olgusal satırı (LABELLED revizyon satırı) zaten yazılmış olmalı.
THIN_MIN_LABELLED_CF = 10_000

_ROW_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_COUNTER_KEYS = ("written", "duplicate", "invalid", "nan", "thinned", "blocked_degraded", "io_error", "rollback_failed")


def _thin_keep(row: Mapping[str, Any]) -> bool:
    """1/2 seyreltme — karşı-olgusal anahtarının kararlı özetinin paritesi (aynı satır her denemede AYNI kararı alır)."""
    key = str(row.get("cf_key") or row.get("row_id") or "")
    return int(stable_id("thin", key)[-1], 16) % 2 == 0


class ExperienceStore(DecisionJournal):
    """Ortak deneyim deposu: toplu, idempotent, kayıpsız döngülü JSONL. Arızası çağıranı ÇÖKERTMEZ (sayılır).

    Akış sözleşmesi SINIF ÖZNİTELİKLERİNDEDİR (2026-09-29, gölge danışman): şema, kabul edilen türler, akış kimliği, sıcak
    dosya adı ve %100 disk baskısında seyreltme. Ana deponun değerleri DEĞİŞMEDİ; türetilmiş akışlar (ör.
    `advice_store.AdviceStore`) yalnız bu öznitelikleri ezer — satırlar iki akış arasında ASLA karışmaz."""

    #: Satır şeması (her satırın `schema` alanı).
    SCHEMA: str = ROW_SCHEMA
    #: Kabul edilen satır türleri (başka tür → RED `KIND`).
    KINDS_ACCEPTED: tuple[str, ...] = tuple(KINDS)
    #: Arşiv akış kimliği ve sıcak dosya adı.
    STREAM_ID: str = STREAM_ID
    HOT_FILE: str = HOT_FILE
    #: %100 disk baskısında seyreltme (ana depo: evet; kapalıysa %100 doğrudan DEGRADED).
    THIN_ENABLED: bool = True

    def __init__(self, root: Path | str, *, hot_max_lines: int = DEFAULT_HOT_MAX_LINES, archive_max_segments: int = 0,
                 code_sha: str | None = None, max_total_mb: float | None = DEFAULT_MAX_TOTAL_MB):
        self.root = Path(root)
        try:
            # kilit anahtarı (yol bazlı, süreç içi) HER örnekte çözümlenmiş yoldan türesin (2026-09-29)
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        archive = SegmentArchive(self.root / ARCHIVE_DIR, stream_id=self.STREAM_ID, record_schema_version=self.SCHEMA,
                                 code_sha=code_sha, max_segments=int(archive_max_segments))
        super().__init__(self.root / self.HOT_FILE, max_lines=int(hot_max_lines), archive=archive)
        mb = float(max_total_mb) if max_total_mb is not None else 0.0
        #: None → tavan yok (yalnız test/araç); yapılandırma 64..10240 MB doğrular.
        self.max_total_bytes: int | None = int(mb * 1024 * 1024) if mb > 0 else None
        self.counters: dict[str, int] = {k: 0 for k in _COUNTER_KEYS}
        #: Defter başına yazılmış ETİKETLİ karşı-olgusal satır sayısı (seyreltme uygunluğu). Kalıcı kaynak çağıranın
        #: imlecidir (`seed_cf_labelled`); depo bu süreçte yazdıklarını ekler. Arşiv her açılışta TARANMAZ.
        self.cf_labelled: dict[str, int] = {}
        self.last_append: dict[str, Any] = {}
        self.state = STATE_OK
        self._archive_bytes: int | None = None
        #: Döngü sonrası sıcak dosyada kalan satır (histerezis alt sınırı; 2026-09-29).
        self.keep_lines = max(0, int(self.max_lines * ROTATE_KEEP_FRACTION)) if self.max_lines > 0 else 0
        #: Kurtarma (yetim segment + bekleyen budama) bekliyor mu — ilk döngü çağrısında ve arşiv hatasından sonra.
        self._recover_pending = True
        self.rotations = 0

    # -------------------------------------------------------------- disk baskısı
    def _archive_total(self) -> int:
        """Arşivin sıkıştırılmış baytı — manifestten (segment AÇILMAZ); döngüden sonra yeniden okunur."""
        if self._archive_bytes is None:
            t = (self.archive.manifest().get("totals") or {}) if self.archive is not None else {}
            try:
                self._archive_bytes = int(t.get("bytes_compressed") or 0)
            except (TypeError, ValueError):
                self._archive_bytes = 0
        return self._archive_bytes

    def disk_bytes(self) -> int:
        """Sıcak dosya boyutu + arşiv manifestindeki sıkıştırılmış bayt toplamı."""
        try:
            hot = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            hot = 0
        return int(hot) + self._archive_total()

    def _state_for(self, used: int) -> str:
        cap = self.max_total_bytes
        if not cap:
            return STATE_OK
        frac = used / cap
        if frac >= DEGRADE_FRACTION:
            return STATE_DEGRADED
        if frac >= THIN_FRACTION:
            return STATE_THINNING if self.THIN_ENABLED else STATE_DEGRADED
        if frac >= WARN_FRACTION:
            return STATE_WARN
        return STATE_OK

    def pressure(self) -> dict[str, Any]:
        """{state, disk_bytes, cap_bytes, fraction} — O(1) (stat + önbellekli manifest toplamı)."""
        used = self.disk_bytes()
        cap = self.max_total_bytes
        self.state = self._state_for(used)
        return {"state": self.state, "disk_bytes": used, "cap_bytes": cap,
                "fraction": round(used / cap, 6) if cap else None}

    def seed_cf_labelled(self, counts: Mapping[str, Any]) -> None:
        """Kalıcı etiketli karşı-olgusal sayaçlarını (çağıranın imleci) yükler; bilinmeyen defter/negatif atlanır."""
        for book, n in (counts or {}).items():
            if str(book) in BOOKS and isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                self.cf_labelled[str(book)] = int(n)

    def cf_labelled_counts(self) -> dict[str, int]:
        return dict(self.cf_labelled)

    # -------------------------------------------------------------- doğrulama
    @classmethod
    def _validate(cls, row: Any) -> tuple[dict[str, Any] | None, str]:
        """(yazılacak satır, neden). Satır ancak gerekirse kopyalanır (çağıranın sözlüğü DEĞİŞMEZ). Şema ve türler sınıf
        özniteliklerinden (`SCHEMA`, `KINDS_ACCEPTED`)."""
        if not isinstance(row, Mapping):
            return None, "NOT_A_MAPPING"
        if row.get("schema") != cls.SCHEMA:
            return None, "SCHEMA"
        if row.get("kind") not in cls.KINDS_ACCEPTED:
            return None, "KIND"                        # `decision` / `outcome_link` ASLA bu akışa girmez
        rid = row.get("row_id")
        if not isinstance(rid, str) or not _ROW_ID_RE.match(rid):
            return None, "ROW_ID"
        if str(row.get("app_mode") or "").upper() != APP_MODE_PAPER:
            return None, "NOT_PAPER"
        if row.get("book") not in BOOKS:
            return None, "BOOK"
        rev = row.get("rev")
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
            return None, "REV"
        did = row.get("decision_id")
        if did is None:
            return {**row, "decision_id": rid}, ""
        if did != rid:
            return None, "DECISION_ID"
        return dict(row), ""

    def _thin_eligible(self, row: Mapping[str, Any]) -> bool:
        return (self.THIN_ENABLED and row.get("kind") == KIND_CF and row.get("rev") == 0
                and self.cf_labelled.get(str(row.get("book")), 0) >= THIN_MIN_LABELLED_CF)

    # -------------------------------------------------------------- yazım
    def append_rows(self, rows: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
        """Toplu yazım — TEK açılış + flush + fsync. Döner: (yazılan, reddedilen). Reddedilen = yazılmayan ve tekrar
        OLMAYAN satırlar (geçersiz, NaN, seyreltilen, DEGRADED, G/Ç hatası); ayrıntı `last_append`, birikimli
        `counters`. G/Ç hatasında bu toplu yazımın kimlikleri işaretlenmez (sonraki adım yeniden dener)."""
        res: dict[str, Any] = {k: 0 for k in _COUNTER_KEYS}
        res.update({"state": None, "error": None, "reasons": {}, "partial_line_repaired": False, "rollback": None})
        with self._lock:
            used = self.disk_bytes()
            state = self._state_for(used)
            lines: list[str] = []
            ids: list[str] = []
            labelled: list[str] = []
            batch: set[str] = set()
            for row in rows:
                r, why = self._validate(row)
                if r is None:
                    res["invalid"] += 1
                    res["reasons"][why] = res["reasons"].get(why, 0) + 1
                    continue
                rid = r["row_id"]
                if rid in self._seen or rid in batch:
                    res["duplicate"] += 1
                    continue
                if state == STATE_DEGRADED:
                    res["blocked_degraded"] += 1
                    continue
                if state == STATE_THINNING and self._thin_eligible(r):
                    if not _thin_keep(r):
                        res["thinned"] += 1
                        continue
                    r["thinned"] = True
                try:
                    line = json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                except ValueError:
                    res["nan"] += 1                    # NaN/sonsuz: satır RED (bare NaN JSON'a ASLA çıkmaz)
                    continue
                except TypeError:
                    res["invalid"] += 1
                    res["reasons"]["NOT_JSON"] = res["reasons"].get("NOT_JSON", 0) + 1
                    continue
                lines.append(line)
                ids.append(rid)
                batch.add(rid)
                if r.get("kind") == KIND_CF and r.get("status") == LABELLED:
                    labelled.append(str(r.get("book")))
            if lines:
                size0: int | None = None
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, "a+b") as fh:
                        fh.seek(0, os.SEEK_END)
                        size = size0 = fh.tell()
                        prefix = b""
                        if size:
                            fh.seek(size - 1)
                            if fh.read(1) != b"\n":
                                prefix = b"\n"         # yarım son satır (çökme): yeni satırlar ona YAPIŞMAZ
                                res["partial_line_repaired"] = True
                        fh.seek(0, os.SEEK_END)
                        fh.write(prefix + ("\n".join(lines) + "\n").encode("utf-8"))
                        fh.flush()
                        os.fsync(fh.fileno())
                except OSError as exc:
                    self.errors += 1
                    res["io_error"] = len(lines)
                    res["error"] = ("%s: %s" % (type(exc).__name__, exc))[:300]
                    if size0 is not None:
                        # GERİ KESME (2026-09-29): bu toplu yazımın diske ulaşmış baytları (yarım/tam satırlar, ayraç)
                        # silinir — yeniden deneme aynı `row_id` ile yeni içerik yazabilir; eski kopya kalmamalı.
                        res["rollback"] = "OK" if self._truncate_to(size0) else "FAILED"
                        if res["rollback"] == "FAILED":
                            res["rollback_failed"] = 1
                    self._line_count = self._count_lines()
                else:
                    self._seen.update(ids)
                    self.appended += len(lines)
                    self._line_count += len(lines)
                    res["written"] = len(lines)
                    for b in labelled:
                        self.cf_labelled[b] = self.cf_labelled.get(b, 0) + 1
            self.state = state
            res["state"] = state
            for k in _COUNTER_KEYS:
                self.counters[k] += int(res[k])
            self.last_append = res
        rejected = res["invalid"] + res["nan"] + res["thinned"] + res["blocked_degraded"] + res["io_error"]
        return int(res["written"]), int(rejected)

    def _truncate_to(self, size: int) -> bool:
        """Dosyayı `size` bayta geri keser (kilit ALTINDA çağrılır). fsync hatası yutulur (kesme sayfa önbelleğinde)."""
        try:
            with open(self.path, "r+b") as fh:
                fh.truncate(int(size))
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            return True
        except OSError:
            return False

    # Miras yazım yolları da AYNI doğrulama/toplu yoldan geçer (karar günlüğü türleri bu akışa giremez).
    def append_decision(self, rec: dict[str, Any]) -> bool:
        return self.append_rows([rec])[0] == 1

    def append_outcome(self, rec: dict[str, Any]) -> bool:
        return self.append_rows([rec])[0] == 1

    def append_many(self, recs: Iterable[dict[str, Any]]) -> int:
        return self.append_rows(list(recs))[0]

    # -------------------------------------------------------------- bakım / okuma
    def rotate(self) -> dict[str, Any]:
        """Kayıpsız, HİSTEREZİSLİ döngü (2026-09-29, inceleme bulgusu). Sıra mirasla aynı: kurtarma → bekleyen budama →
        mühürleme → manifest (bekleyen budama kaydıyla) → budama → kaydı temizle; arşiv yoksa ya da yazım/checksum
        başarısızsa SICAK DOSYA BUDANMAZ. Farklar: (1) sınırın altında (`_line_count <= hot_max_lines`) G/Ç YOK;
        (2) sınır aşılınca dosya `keep_lines` satıra budanır (segment en az `hot_max_lines − keep_lines` satır);
        (3) kurtarma süreç başına bir kez + arşiv hatasından sonra; (4) arşiv bayt önbelleği yalnız döngüden sonra
        tazelenir; (5) `_seen`den yalnız arşive giden bloğun kimlikleri düşer (bellek: ≤ `hot_max_lines` kimlik).
        Arşive gitmiş bir satır yeniden gelirse zararsız bir kopya yazılır; okuma yolu tekilleştirir."""
        res: dict[str, Any] = {"archived": 0, "trimmed": 0, "segment_id": None, "health": "OK", "error": None,
                               "recovered": None}
        if self.archive is None:
            res["health"] = "NO_ARCHIVE_NO_DELETION"   # arşiv yoksa SİLME YOK (miras davranışı)
            return res
        if not self._recover_pending and self._line_count <= self.max_lines:
            return res                                  # sıradan adım: dosya/manifest/segment klasörü okunmaz
        with self._lock:
            try:
                if self._recover_pending:
                    res["recovered"] = self.archive.recover()
                    pending = self.archive.pending_trim()
                    if pending:
                        res["trimmed"] += self._apply_trim(pending)
                        if self.archive.segment_for(str(pending.get("segment_id") or "")) is not None:
                            self.archive.clear_pending_trim()
                        self._reseed_seen_locked()
                    self._recover_pending = False
                    self._archive_bytes = None
                if self._line_count <= self.max_lines:
                    return res
                lines = self._read_lines()
                self._line_count = len(lines)
                if len(lines) <= self.max_lines:
                    return res
                cut = len(lines) - self.keep_lines
                block = lines[:cut]
                meta = self.archive.seal(block)
                self.archive.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": cut,
                                                        "block_sha256": meta["block_sha256"]})
                res["archived"] = cut
                res["segment_id"] = meta["segment_id"]
                res["trimmed"] += self._apply_trim({"n_lines": cut, "block_sha256": meta["block_sha256"]})
                self.archive.clear_pending_trim()
                self.rotations += 1
                self._archive_bytes = None               # yeni segment: toplam bir kez yeniden okunur
                self._drop_seen(block)
            except (ArchiveError, OSError, ValueError) as exc:
                # Arşiv başarısız → BUDAMA YOK. Sessiz kayıp yerine açık alarm; sonraki çağrı kurtarmayı yeniden dener.
                self.archive_errors += 1
                self.last_archive_error = f"{type(exc).__name__}: {exc}"[:300]
                res["health"] = "ARCHIVE_FAILED"
                res["error"] = self.last_archive_error
                self._recover_pending = True
                self._archive_bytes = None
        return res

    def _drop_seen(self, block: list[str]) -> None:
        """Arşive giden bloğun kimliklerini idempotency kümesinden düşürür (kilit altında; sıcak dosya yeniden okunmaz)."""
        for ln in block:
            try:
                r = json.loads(ln)
            except ValueError:
                continue                                 # yarım satır: kimliği zaten kümede değil
            if isinstance(r, dict) and r.get("decision_id"):
                self._seen.discard(str(r["decision_id"]))

    def _reseed_seen(self) -> None:
        with self._lock:
            self._reseed_seen_locked()

    def _reseed_seen_locked(self) -> None:
        """`_seen` = sıcak dosyadaki kimlikler (çağıran kilidi tutar; `threading.Lock` yeniden girilemez)."""
        seen: set[str] = set()
        for r in self.iter_rows():
            if isinstance(r, dict) and r.get("decision_id"):
                seen.add(str(r["decision_id"]))
        self._seen = seen

    def summary(self) -> dict[str, Any]:
        """Sağlık/durum için O(1) özet (dosya TARANMAZ)."""
        return {"schema": self.SCHEMA, "hot_lines": int(self._line_count), "hot_max_lines": int(self.max_lines),
                "hot_keep_lines": int(self.keep_lines), "rotations": int(self.rotations), **self.pressure(),
                "counters": dict(self.counters), "cf_labelled": dict(self.cf_labelled),
                "write_errors": int(self.errors), "archive_errors": int(self.archive_errors),
                "last_archive_error": self.last_archive_error}

    def stats(self) -> dict[str, Any]:
        """Sıcak dosyanın tür/defter dökümü + saklama özeti (durum CLI'ı; sıcak döngü ÇAĞIRMAZ — O(sıcak dosya))."""
        by_kind: dict[str, int] = {}
        by_book: dict[str, int] = {}
        n = 0
        for r in self.iter_rows():
            if not isinstance(r, dict):
                continue
            n += 1
            by_kind[str(r.get("kind"))] = by_kind.get(str(r.get("kind")), 0) + 1
            by_book[str(r.get("book"))] = by_book.get(str(r.get("book")), 0) + 1
        return {**self.summary(), "hot_rows_parsed": n, "hot_by_kind": dict(sorted(by_kind.items())),
                "hot_by_book": dict(sorted(by_book.items())), "retention": self.retention_stats()}


__all__ = ["ARCHIVE_DIR", "DEFAULT_HOT_MAX_LINES", "DEFAULT_MAX_TOTAL_MB", "DEGRADE_FRACTION", "ExperienceStore",
           "HOT_FILE", "PRESSURE_STATES", "ROTATE_KEEP_FRACTION", "STATE_DEGRADED", "STATE_OK", "STATE_THINNING",
           "STATE_WARN", "STREAM_ID", "THIN_FRACTION", "THIN_MIN_LABELLED_CF", "WARN_FRACTION"]
