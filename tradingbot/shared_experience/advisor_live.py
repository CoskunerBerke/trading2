# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — CANLI sarmalayıcı (2026-09-29). Yalnız KAYIT; yalnız `state/<state_dir>/advice/**` altına yazar.

Toplayıcı (`collector.SharedExperienceCollector._flush`) imleç işlemlerinden SONRA, ana depo döngüsünden ÖNCE
`LiveAdvisor.on_flush(yazılan satırlar, …)` çağırır. Buradaki HİÇBİR arıza dışarı sızmaz (sayılır; devre kesici ayrı).

* **Canlı mod.** Bu adımda diske yazılan satırlar (dosya sırası) `AdvisorFold` ile katlanır; tavsiye çekirdekleri
  yazım anı + `meta` ile `AdviceStore`a TEK toplu yazımla (bir fsync) gider.
* **Yetişme modu.** İlk açılış (mevcut depo), kayıp/uyumsuz anlık görüntü, mühür değişimi, katlama arızası ya da
  boşluk tespiti: `StoreReader` ana depoyu (arşiv segmentleri → sıcak dosya) son katlanan satırdan itibaren adım başına
  ≤ `advisor_rebuild_rows_per_step` satır / `advisor_budget_ms` ile TAM TOPLU YAZIMLAR hâlinde okur. Toplayıcının bu
  sırada yazdıkları zaten depodadır; okuyucu son yazılana yetişince canlı moda geçilir. İçerik canlıyla AYNIDIR.
* **Doğum.** `advisor_born_ms` = ilk yetişmeden sonra CANLI katlanan ilk toplu yazımın saati; `advice/advisor_meta.json`a
  bir kez yazılır (mühür başına), sonraki yeniden kurulumlar onu ASLA taşımaz.
* **Anlık görüntü.** `advice/advisor_state.v1.gz` (JSON başlık + gövde sha256 + paketli diziler; pickle YOK; atomik) en
  çok `advisor_snapshot_every_steps` adımda bir ve ana depo döngüsünden sonra, kalan bütçe yetiyorsa.
* **Devre kesici** (toplayıcınınkinden AYRI): art arda 5 istisna ya da art arda 3 adım > 3 × `advisor_budget_ms` →
  `DISABLED_BY_BREAKER` (süreç boyunca). Toplayıcı etkilenmez; yeniden başlatmada anlık görüntüden yetişir.
* **Bellek tavanı.** Tahmini dizin > `advisor_max_index_mb` → DEGRADED (canlı tavsiye durur; katlama eksiksiz sayılmaz).

İnceleme düzeltmeleri (2026-09-30):
* **Yetişme bütçesi.** Yetişme adımları `advisor_catch_up_budget_ms` (varsayılan 1000 ms) kullanır; canlı adımlar
  `advisor_budget_ms`. Üretim boyutlu satırlarda ölçülen hız ~2,7 satır/ms'dir (tasarımın varsaydığı 5000 satır / 250 ms
  DEĞİL): 200 bin satırlık depo 1000 ms ile ~75 turda (~19 sa), 250 ms ile ~300 turda (~3 gün) yetişir. İlk `--check`
  canlı DEĞİL yetişme (`mode: catch_up`, azalan `lag_rows`) görür. Tavsiye deposu döngüsü öngörülürse süresi katlama
  payından DÜŞÜLÜR (adım bütçe yakınında kalır).
* **Okunamayan segment** (eksik / bozuk): okuyucu ATLAMAZ, bekler → görünür durum `WAITING_SEGMENTS`; konum ve doğum
  ilerlemez; her adım yeniden dener. `segments_bad` / `lines_bad` sağlık ve durum dosyasına çıkar.
* **Kayıt engeli.** Tavsiye deposu DEGRADED (disk tavanı) ya da yazım hatası → görünür durum `RECORD_BLOCKED`
  (sağlık, durum dosyası, panel); `advice_emitted` / `advice_written` ayrı sayılır.
* **Yüksek su işareti.** Her tavsiye satırının `meta.target_seq`i katlamanın hedef sıra numarasıdır; açılışta tavsiye
  deposunun SON satırından (sınırlı okuma) okunur. Yeniden kurulum (anlık görüntü kaybı, konum kaybı) zaten yazılmış
  hedefleri YENİDEN YAZMAZ (eskiden bütün geçmiş kopya olarak yeniden yazılıyordu).
* **Doğum geri okuma** yalnız meta → anlık görüntü → tavsiye deposunun SON satırı (sınırlı; tam tarama YOK).
* **Anlık görüntü** art arda `SNAP_FORCE_AFTER` erteleme sonrası ucuz bir adımda ZORLA yazılır (erteleme kilidi yok).
* **Bellek.** Canlıya geçişte okuyucu (sıcak dosya baytları) bırakılır; anlık görüntü kopyasız (memoryview) açılır ve
  büyük geçici tamponlardan sonra ayırıcı belleği işletim sistemine iade edilir (glibc `malloc_trim`).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..core import atomic_write_bytes, atomic_write_text, iso
from . import report as XR
from .advice_store import ADVICE_DIR, ADVICE_HOT_FILE, AdviceStore
from .advisor import (ADVICE_SCHEMA, ADVISOR_ID, ADVISOR_SHA, BOOK_KEYS, LABELS, AdvisorFold, ms_exact)
from .situation import SCHEMA_SHA

log = logging.getLogger(__name__)

STATE_FILE = "advisor_state.v1.gz"
META_FILE = "advisor_meta.json"
STATUS_FILE = "advisor_status.json"
SUMMARY_FILE = "walkforward_summary.json"
STATUS_SCHEMA = "shared_experience_advisor_status_v1"
META_SCHEMA = "shared_experience_advisor_meta_v1"
SNAPSHOT_SCHEMA = "shared_experience_advisor_snapshot_v1"
MODE_LIVE, MODE_CATCH_UP = "live", "catch_up"
A_OK, A_BREAKER, A_DEGRADED = "OK", "DISABLED_BY_BREAKER", "DEGRADED"
#: Görünür durumlar (iç durum OK iken; sağlık / durum dosyası / panel): okunamayan ana depo segmenti beklenir; tavsiye
#: yazılamıyor (tavsiye deposu DEGRADED ya da yazım hatası). İkisi de katlamayı DURDURMAZ ve kararı ETKİLEMEZ.
S_WAITING_SEGMENTS, S_RECORD_BLOCKED = "WAITING_SEGMENTS", "RECORD_BLOCKED"
BREAKER_ERRORS = 5
BREAKER_OVERRUNS = 3
OVERRUN_FACTOR = 3.0
RETRY_MAX = 2000
STEP_MS_KEEP = 50
#: Art arda bu kadar erteleme sonrası anlık görüntü, adımın kendisi ucuzsa (≤ bütçe) ZORLA yazılır.
SNAP_FORCE_AFTER = 5
#: Tavsiye deposunun son satırı için sıcak dosyanın sonundan ayrıştırılan en çok bayt.
TAIL_READ_BYTES = 256 * 1024
STATUS_MAX_BYTES = 65_536
#: Sabit şerit (panel ve CLI AYNEN basar; panel yerel kopyası test eşitliğine bağlı).
BANNER_TR = ("yalnız KAYIT — karar değişmez; ara bakış kanıt değildir; başarı yalnız önceden kayıtlı bakışlarda "
             "(L1/L2/L3)")
_ROW_ID_LEN = 16
_COUNTERS = ("rebuilds", "snapshots", "snapshot_errors", "snapshot_deferred", "snapshot_loaded", "snapshot_rejected",
             "catch_up_steps", "catch_up_rows", "live_batches", "live_deferred", "gaps", "advice_written",
             "advice_duplicate", "advice_dropped", "advice_retry", "advice_io_errors", "watermark_lost", "status_errors",
             "meta_errors", "degraded_steps", "advice_emitted", "advice_skipped_hwm", "segments_bad", "lines_bad",
             "waiting_segment_steps", "record_blocked_steps", "snapshot_forced", "memory_trims")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _after_needle(data: bytes, needle: bytes, start: int = 0) -> int | None:
    """`needle`i içeren İLK satırın sonundaki bayt konumu (yeni satırdan sonra) ya da None."""
    i = data.find(needle, start)
    if i < 0:
        return None
    j = data.find(b"\n", i)
    return len(data) if j < 0 else j + 1


def _needle(row_id: str) -> bytes:
    return b'"row_id":"' + str(row_id).encode("ascii", "replace") + b'"'


def _last_row_id(batch: list[Mapping[str, Any]]) -> str | None:
    for r in reversed(batch):
        rid = r.get("row_id") if isinstance(r, Mapping) else None
        if isinstance(rid, str) and len(rid) == _ROW_ID_LEN:
            return rid
    return None


def _last_advice_row(data: bytes, *, whole: bool) -> dict[str, Any] | None:
    """Bayt bloğundaki SON geçerli tavsiye satırı (bu mühür). `whole=False` → ilk (yarım olabilecek) satır atlanır."""
    lines = data.split(b"\n")
    if not whole and lines:
        lines = lines[1:]
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("schema") == ADVICE_SCHEMA and r.get("advisor_sha") == ADVISOR_SHA:
            return r
    return None


def _malloc_trim() -> bool:
    """glibc'de serbest yığın sayfalarını işletim sistemine iade eder (büyük geçici tamponlardan sonra RSS). Başka
    platformda / hata → False (hiçbir şey olmaz)."""
    try:
        import ctypes
        import ctypes.util
        name = ctypes.util.find_library("c")
        if not name:
            return False
        return bool(ctypes.CDLL(name).malloc_trim(0))
    except Exception:  # noqa: BLE001
        return False


def _max_seq(segs: list[dict[str, Any]]) -> int:
    out = 0
    for s in segs:
        v = s.get("seq")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out = max(out, int(v))
    return out


# ============================================================================ ana depo okuyucusu (yetişme)
class StoreReader:
    """Ana deponun YAZIM sırası (arşiv segmentleri `seq` sırasıyla → sıcak dosya), `after_row_id`den SONRA, TAM TOPLU
    YAZIMLAR hâlinde (aynı `recorded_at`li ardışık satırlar). SALT OKUR.

    Konum: son tamamen verilen toplu yazımın son satırı (`last_row_id`) + bulunduğu kaynak. Segmentler değişmez (konum
    bayt ofsetidir); sıcak dosya döngüyle baştan budanabilir → her çağrıda döngü tespit edilir ve konum `row_id` ile
    yeniden bulunur. `watermark_seq`: son satırı tutan segment (None → sıcak dosya; o zaman `seq_floor`dan büyük
    segmentler, sonra sıcak dosya aranır). Bulunamazsa `lost` (çağıran baştan kurar).

    (2026-09-30, inceleme bulgusu) Manifestte listelenen ama okunamayan (eksik / bozuk / sha256 uyuşmayan) bir segment
    ATLANMAZ: okuyucu orada DURUR (`blocked`), yarım toplu yazımı vermez, konumu ilerletmez; her çağrıda yeniden dener.
    Eskiden atlanıyordu: saatlik yedekten (segmentsiz) geri yüklemede yeniden kurulum geçmişin ~%2'sinden yapılıp OK
    görünüyordu. Sayaçlar (`segments_bad`, `lines_bad`) çağıranca toplanıp dışa verilir."""

    def __init__(self, root: Path | str, *, after_row_id: str | None = None, watermark_seq: int | None = None,
                 seq_floor: int | None = None) -> None:
        self.root = Path(root)
        self.last_row_id = after_row_id
        self.last_recorded_at: str | None = None
        self.watermark_seq = watermark_seq
        self._floor = seq_floor
        self._need_locate = after_row_id is not None
        self._src: tuple[str, int | None] | None = None        # ("seg", seq) | ("hot", None)
        self._data: bytes = b""
        self._pos = 0
        self._hot_floor = 0
        self.at_end = False
        self.lost = False
        self.rows = 0
        self.lines_bad = 0
        self.segments_bad = 0
        #: okunamayan segment: {"seq", "file", "reason"} — okuyucu orada bekler (None → engel yok)
        self.blocked: dict[str, Any] | None = None

    # ------------------------------------------------------------------ kaynaklar
    def _segs(self) -> list[dict[str, Any]]:
        return [s for s in XR._segments(XR.manifest(self.root)) if isinstance(s.get("seq"), (int, float))]

    def _load(self, seg: Mapping[str, Any]) -> bytes | None:
        """Segment baytları; okunamazsa None ve `blocked` kurulur (çağıran DURUR)."""
        name = str(seg.get("file") or "")
        reason = None
        if not name or "/" in name or "\\" in name or name.startswith("."):
            reason = "BAD_NAME"
        else:
            try:
                blob = (self.root / XR.ARCHIVE_DIR / XR.SEGMENTS_DIR / name).read_bytes()
                if hashlib.sha256(blob).hexdigest() != str(seg.get("sha256") or ""):
                    reason = "SHA256_MISMATCH"
                else:
                    return gzip.decompress(blob)
            except FileNotFoundError:
                reason = "MISSING"
            except (OSError, EOFError, ValueError) as exc:
                reason = "UNREADABLE: %s" % type(exc).__name__
        self.segments_bad += 1
        self.blocked = {"seq": int(seg.get("seq") or 0), "file": name[:120], "reason": reason}
        return None

    def _hot(self) -> bytes:
        try:
            return (self.root / XR.HOT_FILE).read_bytes()
        except OSError:
            return b""

    def _locate(self, segs: list[dict[str, Any]], *, above: int | None, include_wm: bool) -> bool:
        """`last_row_id`i içeren satırın hemen sonrasına konumlanır (segmentler → sıcak dosya)."""
        rid = self.last_row_id
        wm = self.watermark_seq if include_wm else None
        if wm is not None:
            cands = [s for s in segs if int(s["seq"]) >= wm]
        elif above is not None:
            cands = [s for s in segs if int(s["seq"]) > above]
        else:
            cands = list(segs)
        if rid is None:                                       # hiçbir şey okunmadı: ilk adaydan başla
            if cands:
                data = self._load(cands[0])
                if data is None:
                    return False                             # ilk segment okunamıyor: BEKLE (sıcak dosyaya ATLAMA)
                self._src, self._data, self._pos = ("seg", int(cands[0]["seq"])), data, 0
                return True
            self._src, self._data, self._pos, self._hot_floor = ("hot", None), self._hot(), 0, _max_seq(segs)
            return True
        nd = _needle(rid)
        for s in cands:
            data = self._load(s)
            if data is None:
                return False                                 # konum bu segmentte olabilir: karar verilemez, BEKLE
            off = _after_needle(data, nd)
            if off is not None:
                self._src, self._data, self._pos = ("seg", int(s["seq"])), data, off
                return True
        hot = self._hot()
        off = _after_needle(hot, nd)
        if off is not None:
            self._src, self._data, self._pos, self._hot_floor = ("hot", None), hot, off, _max_seq(segs)
            return True
        self.lost = True
        return False

    def _prepare(self, segs: list[dict[str, Any]]) -> bool:
        self.blocked = None
        if self._need_locate:
            ok = self._locate(segs, above=self._floor, include_wm=True)
            self._need_locate = self.blocked is not None      # engellendiyse aynı parametrelerle yeniden denenir
            return ok
        if self._src is None:
            return self._locate(segs, above=self._floor, include_wm=False)
        if self._src[0] == "hot":
            mx = _max_seq(segs)
            if mx > self._hot_floor:                          # döngü: baş blok yeni segment(ler)e taşındı
                return self._locate(segs, above=self._hot_floor, include_wm=False)
            hot = self._hot()
            ok = len(hot) >= self._pos
            if ok and self.last_row_id is not None and self._pos > 0:
                k = hot.rfind(b"\n", 0, self._pos - 1)
                ok = _needle(self.last_row_id) in hot[k + 1:self._pos]
            if not ok:
                return self._locate(segs, above=self._hot_floor, include_wm=False)
            self._data = hot
        return True

    def _lines(self, segs: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], tuple, int, bytes, int]]:
        """(satır, kaynak, satır sonu ofseti, kaynak baytı, sıcak taban) — mevcut konumdan başlayarak."""
        src, data, pos = self._src, self._data, self._pos
        hot_floor = self._hot_floor
        while True:
            n = len(data)
            while pos < n:
                j = data.find(b"\n", pos)
                if j < 0:
                    if src[0] == "hot":
                        break                                  # yarım son satır (yazım sürüyor/çökme): tamamlanmamış
                    end, line = n, data[pos:]
                else:
                    end, line = j + 1, data[pos:j]
                pos = end
                s = line.strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except ValueError:
                    self.lines_bad += 1
                    continue
                if isinstance(row, dict):
                    yield row, src, end, data, hot_floor
            if src[0] == "hot":
                return
            nxt = [s for s in segs if int(s["seq"]) > int(src[1])]
            if nxt:
                cand = nxt[0]
                loaded = self._load(cand)
                if loaded is None:
                    return                                   # listelenen segment okunamıyor: DUR (atlama YOK)
                src, data, pos = ("seg", int(cand["seq"])), loaded, 0
            else:
                src, data, pos = ("hot", None), self._hot(), 0
                hot_floor = _max_seq(segs)

    def batches(self) -> Iterator[list[dict[str, Any]]]:
        """TAM toplu yazımları verir; konum yalnız VERİLEN toplu yazımın sonuna ilerler."""
        self.at_end = False
        segs = self._segs()
        if not self._prepare(segs):
            return
        batch: list[dict[str, Any]] = []
        cur = object()
        last: tuple | None = None
        for row, src, end, data, hot_floor in self._lines(segs):
            ra = row.get("recorded_at")
            if batch and ra != cur:
                self._commit(batch, last)
                yield batch
                batch = []
            batch.append(row)
            cur = ra
            last = (src, end, data, hot_floor)
        if self.blocked is not None:
            return                                           # yarım toplu yazım (segment sınırı) VERİLMEZ; konum aynı
        self.at_end = True
        if batch:
            self._commit(batch, last)
            yield batch

    def _commit(self, batch: list[dict[str, Any]], last: tuple | None) -> None:
        if last is not None:
            self._src, self._pos, self._data, self._hot_floor = last[0], last[1], last[2], last[3]
            self.watermark_seq = last[0][1] if last[0][0] == "seg" else None
        rid = _last_row_id(batch)
        if rid is not None:
            self.last_row_id = rid
        self.last_recorded_at = batch[-1].get("recorded_at") if batch else self.last_recorded_at
        self.rows += len(batch)

    def seq_floor(self) -> int:
        """Konumdaki satır sıcak dosyadaysa: o anki en büyük segment `seq`i (alt sınır)."""
        return int(self._hot_floor if (self._src is not None and self._src[0] == "hot") else (self._floor or 0))


# ============================================================================ canlı danışman
class LiveAdvisor:
    """Toplayıcının içinde (ana iş parçacığı) yalnız KAYIT. `on_flush` ASLA istisna atmaz."""

    def __init__(self, xp_root: Path | str, *, settings: Any, code_sha: str | None = None,
                 config_hash: str | None = None, main_store: Any = None) -> None:
        self.root = Path(xp_root)
        self.dir = self.root / ADVICE_DIR
        self.budget_ms = float(getattr(settings, "advisor_budget_ms", 250))
        self.catch_up_budget_ms = float(getattr(settings, "advisor_catch_up_budget_ms", 1000))
        self.rebuild_rows = int(getattr(settings, "advisor_rebuild_rows_per_step", 5000))
        self.snap_every = int(getattr(settings, "advisor_snapshot_every_steps", 60))
        self.max_index_mb = float(getattr(settings, "advisor_max_index_mb", 96))
        self.code_sha, self.config_hash = code_sha, config_hash
        self._main = main_store
        self.store = AdviceStore(self.dir, hot_max_lines=int(getattr(settings, "advice_hot_max_lines", 2000)),
                                 code_sha=code_sha, max_total_mb=float(getattr(settings, "advice_max_total_mb", 256)))
        self.store.load_seen()
        try:
            self.store.rotate()                              # çökme kurtarması (ekleme yapılmadan önce)
        except Exception:  # noqa: BLE001
            pass
        self.state = A_OK
        self.mode = MODE_CATCH_UP
        self.fold: AdvisorFold | None = None
        self.reader: StoreReader | None = None
        self.c: dict[str, int] = {k: 0 for k in _COUNTERS}
        #: tavsiye deposunun SON satırı (bu mühür; sınırlı okuma): yüksek su işareti ve doğum geri okuması
        tail = self._advice_tail()
        meta_t = (tail or {}).get("meta") if isinstance((tail or {}).get("meta"), dict) else {}
        ts = meta_t.get("target_seq") if isinstance(meta_t, dict) else None
        self._adv_hwm: int = int(ts) if isinstance(ts, int) and not isinstance(ts, bool) else 0
        self._tail_born: int | None = (int(tail["advisor_born_ms"]) if isinstance((tail or {}).get("advisor_born_ms"), int)
                                       else None)
        self.born_ms: int | None = self._load_born()
        self.steps = 0
        self.errors = 0
        self._consec_errors = 0
        self._consec_overruns = 0
        self._step_ms: deque = deque(maxlen=STEP_MS_KEEP)
        self._out: list[dict[str, Any]] = []
        self._retry: list[dict[str, Any]] = []
        self._pos_row: str | None = None
        self._pos_ra: str | None = None
        self._pos_floor: int | None = None
        self._appended_seen: int | None = None
        self._known_seq: int | None = None
        self._known_rot: int | None = None
        self._rot_at_snap: int | None = None
        self._steps_since_snap = 0
        self._snap_ms_est = 200.0
        self._snap_deferred_consec = 0
        self._rot_ms_est = 250.0
        self._ms_per_row = 0.5
        self._append_ms_per_row = 0.06
        self._no_snapshot_once = False
        self.advised_last = 0
        self.late_last = 0
        self.last_error: str | None = None
        self.last_step_at: str | None = None
        #: okunamayan ana depo segmenti (okuyucu bekliyor) — None → yok
        self.waiting_segment: dict[str, Any] | None = None
        #: tavsiye yazılamıyor: "STORE_DEGRADED" | "WRITE_ERROR" | None
        self.record_blocked: str | None = None

    # ------------------------------------------------------------------ doğum
    def _meta_path(self) -> Path:
        return self.dir / META_FILE

    def _load_born(self) -> int | None:
        """Doğum: `advisor_meta.json` (bu mühür) → yoksa tavsiye deposunun SON satırı (doğumdan sonraki her satır onu
        taşır). Tavsiye deposu TARANMAZ (2026-09-30 inceleme bulgusu: meta yokken her açılışta bütün depo turun içinde
        açılıyordu — 55 bin satırda ~3,4 s). Anlık görüntü başlığındaki doğum `_init_fold`da uygulanır."""
        doc = XR._read_json_ro(self._meta_path(), max_bytes=1 << 16)
        ent = ((doc or {}).get("advisors") or {}).get(ADVISOR_SHA) if isinstance(doc, dict) else None
        if isinstance(ent, dict) and isinstance(ent.get("advisor_born_ms"), int):
            return int(ent["advisor_born_ms"])
        born = self._tail_born
        if born is not None:
            self._write_meta(born)
        return born

    def _advice_tail(self) -> dict[str, Any] | None:
        """Tavsiye deposunun SON satırı (bu mühür): sıcak dosyanın son ≤ `TAIL_READ_BYTES` baytı; orada yoksa en son
        arşiv segmenti. SINIRLI maliyet; arıza → None."""
        try:
            p = self.dir / ADVICE_HOT_FILE
            if p.exists():
                # sıcak dosya `advice_hot_max_lines` ile sınırlı (≈ birkaç MB; `load_seen` onu zaten okur); yalnız son
                # `TAIL_READ_BYTES` ayrıştırılır
                data = p.read_bytes()
                r = _last_advice_row(data[-TAIL_READ_BYTES:], whole=len(data) <= TAIL_READ_BYTES)
                del data
                if r is not None:
                    return r
            segs = [x for x in XR._segments(XR.manifest(self.dir)) if isinstance(x.get("seq"), (int, float))]
            if segs:
                last = max(segs, key=lambda x: int(x["seq"]))
                name = str(last.get("file") or "")
                if name and "/" not in name and "\\" not in name and not name.startswith("."):
                    blob = (self.dir / XR.ARCHIVE_DIR / XR.SEGMENTS_DIR / name).read_bytes()
                    return _last_advice_row(gzip.decompress(blob), whole=True)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _write_meta(self, born: int) -> None:
        doc = XR._read_json_ro(self._meta_path(), max_bytes=1 << 16)
        doc = doc if isinstance(doc, dict) and isinstance(doc.get("advisors"), dict) else {"schema": META_SCHEMA,
                                                                                             "advisors": {}}
        doc["schema"] = META_SCHEMA
        doc["advisors"][ADVISOR_SHA] = {"advisor_id": ADVISOR_ID, "advisor_born_ms": int(born),
                                        "advisor_born_at": XR._iso_ms(int(born)), "written_at": iso(_now())}
        try:
            atomic_write_text(self._meta_path(), json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True))
        except OSError:
            self.c["meta_errors"] += 1

    def _set_born(self, clock: int) -> None:
        self.born_ms = int(clock)
        if self.fold is not None:
            self.fold.born_ms = self.born_ms
        self._write_meta(self.born_ms)

    # ------------------------------------------------------------------ ana depo segment tabanı
    def _seq_now(self) -> int:
        """Ana arşivin en büyük `seq`i — yalnız döngüden sonra yeniden okunur (alt sınır olarak güvenli)."""
        rot = int(getattr(self._main, "rotations", 0) or 0) if self._main is not None else None
        if self._known_seq is None or rot is None or rot != self._known_rot:
            self._known_seq = _max_seq(XR._segments(XR.manifest(self.root)))
            self._known_rot = rot
        return int(self._known_seq)

    # ------------------------------------------------------------------ anlık görüntü
    def _state_path(self) -> Path:
        return self.dir / STATE_FILE

    def _read_snapshot(self) -> tuple[dict[str, Any], AdvisorFold] | None:
        p = self._state_path()
        if not p.exists():
            return None
        try:
            raw = gzip.decompress(p.read_bytes())
            nl = raw.index(b"\n")
            hdr = json.loads(raw[:nl].decode("utf-8"))
            body = memoryview(raw)[nl + 1:]                  # (2026-09-30) kopyasız: geçici tepe ~1 gövde daha az
            if (not isinstance(hdr, dict) or hdr.get("schema") != SNAPSHOT_SCHEMA or hdr.get("advisor_sha") != ADVISOR_SHA
                    or hdr.get("situation_schema_sha") != SCHEMA_SHA
                    or hashlib.sha256(body).hexdigest() != hdr.get("body_sha256")):
                raise ValueError("snapshot header/sha")
            fold = AdvisorFold.from_state(body)
            del body, raw
        except (OSError, ValueError, KeyError, EOFError, TypeError) as exc:
            self.c["snapshot_rejected"] += 1
            self.last_error = ("SNAPSHOT_REJECTED: %s" % exc)[:200]
            return None
        self.c["snapshot_loaded"] += 1
        if _malloc_trim():
            self.c["memory_trims"] += 1
        return hdr, fold

    def _write_snapshot(self, *, reset_estimate: bool = False) -> None:
        if self.fold is None:
            return
        t0 = time.perf_counter()
        if self.mode == MODE_LIVE or self.reader is None:
            last_row, last_ra, wm = self._pos_row, self._pos_ra, None
            floor = self._pos_floor if self._pos_floor is not None else self._seq_now()
        else:
            last_row, last_ra, wm = self.reader.last_row_id, self.reader.last_recorded_at, self.reader.watermark_seq
            floor = self.reader.seq_floor()
        body = self.fold.state_bytes()
        hdr = {"schema": SNAPSHOT_SCHEMA, "advisor_id": ADVISOR_ID, "advisor_sha": ADVISOR_SHA,
               "situation_schema_sha": SCHEMA_SHA, "snapshot_schema": SNAPSHOT_SCHEMA,
               "batch_clock_ms": self.fold.batch_clock, "last_row_id": last_row, "last_recorded_at": last_ra,
               "watermark_seq": wm, "archive_max_seq": floor, "advisor_born_ms": self.born_ms,
               "body_sha256": hashlib.sha256(body).hexdigest(), "created_at": iso(_now()), "mode": self.mode}
        blob = gzip.compress(json.dumps(hdr, ensure_ascii=True, sort_keys=True).encode("ascii") + b"\n" + body,
                             compresslevel=1, mtime=0)
        atomic_write_bytes(self._state_path(), blob)
        self.c["snapshots"] += 1
        self._steps_since_snap = 0
        self._snap_deferred_consec = 0
        self._rot_at_snap = int(getattr(self._main, "rotations", 0) or 0) if self._main is not None else None
        ms = (time.perf_counter() - t0) * 1000.0
        # zorla yazımdan sonra bayat tahmin GERÇEK ölçümle değişir (yoksa hareketli ortalama aylarca kapıyı kapalı tutar)
        self._snap_ms_est = ms if reset_estimate else 0.7 * self._snap_ms_est + 0.3 * ms

    def _maybe_snapshot(self, t0: float, budget: float, *, force: bool = False) -> None:
        rot = int(getattr(self._main, "rotations", 0) or 0) if self._main is not None else None
        due = force or self._steps_since_snap >= self.snap_every or (rot is not None and self._rot_at_snap is not None
                                                                      and rot != self._rot_at_snap)
        if not due or self.fold is None:
            return
        spent = (time.perf_counter() - t0) * 1000.0
        left = OVERRUN_FACTOR * budget - spent
        if left < self._snap_ms_est and not force:
            # (2026-09-30, inceleme bulgusu) tahmin yalnız yazımdan sonra güncellendiği için erteleme bir kez başlayınca
            # süreç boyunca sürebiliyordu. Art arda SNAP_FORCE_AFTER ertelemeden sonra adımın kendisi ucuzsa (≤ bütçe)
            # anlık görüntü ZORLA yazılır: en çok BİR aşım (kesici art arda 3 ister), sonra tahmin gerçek ölçümle düzelir.
            if self._snap_deferred_consec >= SNAP_FORCE_AFTER and spent <= budget:
                self.c["snapshot_forced"] += 1
                force = True
            else:
                self.c["snapshot_deferred"] += 1              # bütçe yetmiyor: sonraki adım
                self._snap_deferred_consec += 1
                return
        try:
            self._write_snapshot(reset_estimate=force)
        except Exception as exc:  # noqa: BLE001 — anlık görüntü isteğe bağlıdır (yeniden kurulabilir)
            self.c["snapshot_errors"] += 1
            self.last_error = ("SNAPSHOT_WRITE: %s" % exc)[:200]

    # ------------------------------------------------------------------ kurulum / mod geçişleri
    def _init_fold(self) -> None:
        snap = None if self._no_snapshot_once else self._read_snapshot()
        self._no_snapshot_once = False
        if snap is not None:
            hdr, fold = snap
            if self.born_ms is None and isinstance(hdr.get("advisor_born_ms"), int):
                self._set_born(int(hdr["advisor_born_ms"]))
            fold.born_ms = self.born_ms
            self.fold = fold
            self.reader = StoreReader(self.root, after_row_id=hdr.get("last_row_id"),
                                      watermark_seq=hdr.get("watermark_seq"), seq_floor=hdr.get("archive_max_seq"))
        else:
            self.fold = AdvisorFold(born_ms=self.born_ms)
            self.reader = StoreReader(self.root)
            self.c["rebuilds"] += 1
        self.mode = MODE_CATCH_UP
        self._steps_since_snap = 0
        self._rot_at_snap = int(getattr(self._main, "rotations", 0) or 0) if self._main is not None else None

    def _to_catch_up(self) -> None:
        self.mode = MODE_CATCH_UP
        self.reader = StoreReader(self.root, after_row_id=self._pos_row,
                                  seq_floor=self._pos_floor if self._pos_floor is not None else 0)

    # ------------------------------------------------------------------ yayın
    def _emit(self, rows: list[dict[str, Any]], mode: str, seq0: int | None = None) -> None:
        """Tavsiye çekirdeklerine yazım anı + `meta` ekler. `seq0`: ilk satırın hedef sıra numarası (katlama sırası;
        ardışık). Numarası yüksek su işaretinin (depoda zaten yazılmış son hedef) altında kalan satır YAZILMAZ."""
        if not rows:
            return
        now = _now()
        stamp = now.isoformat()
        keep: list[dict[str, Any]] = []
        for i, r in enumerate(rows):
            seq = int(seq0) + i if seq0 is not None else None
            if seq is not None and seq <= self._adv_hwm:
                self.c["advice_skipped_hwm"] += 1             # yeniden kurulum: bu hedef depoda zaten var
                continue
            b = ms_exact(r.get("batch_recorded_at"))
            lag = round((int(now.timestamp() * 1000) - b) / 1000.0, 3) if b is not None else None
            r["recorded_at"] = stamp
            r["meta"] = {"computed_at": stamp, "lag_s": lag, "fold_mode": mode, "code_sha": self.code_sha,
                         "config_hash": self.config_hash, "target_seq": seq}
            if r.get("state") == "LATE":
                self.late_last += 1
            else:
                self.advised_last += 1
            keep.append(r)
        self.c["advice_emitted"] += len(keep)
        self._out.extend(keep)

    def _flush_advice(self) -> None:
        rows = self._retry + self._out
        self._out = []
        self._retry = []
        if not rows:
            return
        t0 = time.perf_counter()
        w, _rej = self.store.append_rows(rows)
        if len(rows) >= 20:                                  # yazım maliyeti tahmini (yetişme bütçesi ayırır)
            self._append_ms_per_row = (0.7 * self._append_ms_per_row
                                       + 0.3 * (time.perf_counter() - t0) * 1000.0 / len(rows))
        la = self.store.last_append or {}
        self.c["advice_written"] += int(w)
        self.c["advice_duplicate"] += int(la.get("duplicate") or 0)
        if int(la.get("io_error") or 0) or int(la.get("blocked_degraded") or 0):
            self.c["advice_io_errors"] += 1
            self.record_blocked = "STORE_DEGRADED" if int(la.get("blocked_degraded") or 0) else "WRITE_ERROR"
            seen = self.store._seen
            keep = [r for r in rows if str(r.get("row_id")) not in seen]
            if len(keep) > RETRY_MAX:
                self.c["advice_dropped"] += len(keep) - RETRY_MAX   # `verify` yeniden hesaplar
                keep = keep[-RETRY_MAX:]
            self._retry = keep
            self.c["advice_retry"] += len(keep)
        else:
            self.record_blocked = None
            seqs = [m.get("target_seq") for m in (r.get("meta") for r in rows) if isinstance(m, dict)]
            seqs = [x for x in seqs if isinstance(x, int) and not isinstance(x, bool)]
            if seqs:
                self._adv_hwm = max(self._adv_hwm, max(seqs))    # bu numaraya kadar depoda (yeniden kurulum atlar)

    def _check_record(self) -> None:
        """Kayıt engeli yazım olmayan adımlarda da görünür: tavsiye deposunun disk baskısı DEGRADED ise."""
        if self._retry:
            self.record_blocked = self.record_blocked or "WRITE_ERROR"
            return
        try:
            st = (self.store.pressure() or {}).get("state")
        except Exception:  # noqa: BLE001
            st = None
        if st == "DEGRADED":
            self.record_blocked = "STORE_DEGRADED"
        elif self.record_blocked == "STORE_DEGRADED":
            self.record_blocked = None

    # ------------------------------------------------------------------ katlama
    def _fold_live(self, written: list[dict[str, Any]]) -> None:
        t0 = time.perf_counter()
        if self.born_ms is None:
            rec = ms_exact(written[0].get("recorded_at"))
            prev = self.fold.batch_clock
            clock = rec if prev is None else (max(prev, rec) if rec is not None else prev)
            if clock is not None:
                self._set_born(clock)
        res = self.fold.fold_rows(written)
        self._emit(res.advice_rows, MODE_LIVE, res.first_target_seq)
        rid = _last_row_id(written)
        if rid is not None:
            self._pos_row = rid
            self._pos_ra = written[-1].get("recorded_at")
            self._pos_floor = self._seq_now()
        self.c["live_batches"] += 1
        ms = (time.perf_counter() - t0) * 1000.0
        self._ms_per_row = 0.8 * self._ms_per_row + 0.2 * (ms / max(1, len(written)))

    def _catch_up(self, t0: float, budget: float) -> None:
        rd = self.reader
        if rd is None:
            rd = self.reader = StoreReader(self.root, after_row_id=self._pos_row, seq_floor=self._pos_floor)
        n = 0
        done_all = False
        it = rd.batches()
        st = self.store
        for batch in it:
            res = self.fold.fold_batch(batch)
            self._emit(res.advice_rows, MODE_CATCH_UP, res.first_target_seq)
            n += len(batch)
            # (2026-09-30) bütçe, bu adımın tavsiye yazımını (JSON + fsync) da kapsar: bekleyen satırların tahmini
            # yazım süresi ayrılır → adım bütçeye yakın biter (eskiden ~%20 aşıyordu). Tavsiye deposu döngüsü bu adımda
            # gerekecekse (sıcak dosya + bekleyenler > tavan) onun tahmini süresi de ayrılır (inceleme bulgusu: döngü
            # adımları 0,8–1,1 s sürüyordu).
            pend = len(self._out) + len(self._retry)
            pend_ms = pend * self._append_ms_per_row
            if int(getattr(st, "_line_count", 0) or 0) + pend > int(getattr(st, "max_lines", 0) or 0) > 0:
                pend_ms += self._rot_ms_est
            if n >= self.rebuild_rows or (time.perf_counter() - t0) * 1000.0 + pend_ms >= budget:
                break
        else:
            done_all = True
        it.close()
        self.c["catch_up_steps"] += 1
        self.c["catch_up_rows"] += n
        # okuyucu sayaçları dışa (durum dosyası / sağlık) toplanır; okunamayan segment → bekle (ATLAMA YOK)
        self.c["segments_bad"] += int(rd.segments_bad)
        self.c["lines_bad"] += int(rd.lines_bad)
        rd.segments_bad = rd.lines_bad = 0
        if rd.blocked is not None:
            if self.waiting_segment is None:
                log.warning("gölge danışman ana depo segmentini BEKLİYOR (atlanmaz; karar ETKİLENMEZ): %s", rd.blocked)
            self.waiting_segment = dict(rd.blocked)
            self.c["waiting_segment_steps"] += 1
        else:
            self.waiting_segment = None
        if rd.lost:
            self.c["watermark_lost"] += 1
            self.fold, self.reader = None, None
            self._no_snapshot_once = True                   # konum bulunamadı: baştan yeniden kur
            self._pos_row = self._pos_ra = None
            self._pos_floor = None
            return
        self._pos_row, self._pos_ra = rd.last_row_id, rd.last_recorded_at
        if done_all and rd.at_end and rd.blocked is None:
            self.mode = MODE_LIVE
            self._pos_floor = rd.seq_floor() if rd.watermark_seq is None else None
            if self._pos_floor is None:
                self._pos_floor = int(rd.watermark_seq) - 1
            if self._main is not None:
                self._appended_seen = int(getattr(self._main, "appended", 0) or 0)
            # (2026-09-30) okuyucu bırakılır: sıcak dosyanın bayt kopyasını canlı modda tutmaz (bellek)
            self.reader = None
            if _malloc_trim():
                self.c["memory_trims"] += 1

    # ------------------------------------------------------------------ genel arayüz
    def on_flush(self, written: list[dict[str, Any]], *, batch_recorded_at: Any = None, now: Any = None,
                 budget_ms: float | None = None) -> dict[str, Any]:
        """Toplayıcının bu adımda DİSKE yazdığı satırlar (dosya sırası). ASLA istisna atmaz."""
        t0 = time.perf_counter()
        self.steps += 1
        self._steps_since_snap += 1
        self.advised_last = self.late_last = 0
        self.last_step_at = iso(now) if isinstance(now, datetime) else iso(_now())
        budget = float(budget_ms if budget_ms is not None else self.budget_ms)
        if self.state == A_BREAKER:
            return {"ms": 0.0, "state": self.state}
        if self.state == A_DEGRADED:
            self.c["degraded_steps"] += 1
            self._write_status()
            return {"ms": round((time.perf_counter() - t0) * 1000.0, 3), "state": self.state}
        try:
            if self.fold is None:
                self._init_fold()
            if self._main is not None:
                app = int(getattr(self._main, "appended", 0) or 0)
                if (self.mode == MODE_LIVE and self._appended_seen is not None
                        and app - self._appended_seen != len(written)):
                    self.c["gaps"] += 1                      # katlanmamış satır var (ör. adım kancadan önce düştü)
                    self._to_catch_up()
                self._appended_seen = app
            if self.mode == MODE_LIVE and written:
                if len(written) * self._ms_per_row > OVERRUN_FACTOR * budget:
                    self.c["live_deferred"] += 1              # içerik aynı kalır (§4.5); okuyucu depodan katlar
                    self._to_catch_up()
                else:
                    self._fold_live(written)
            if self.mode == MODE_CATCH_UP and self.fold is not None:
                budget = max(budget, self.catch_up_budget_ms)     # yetişme adımı: kendi (daha büyük) bütçesi
                self._catch_up(t0, budget)
            self._flush_advice()
            rot0, tr = self.store.rotations, time.perf_counter()
            self.store.rotate()
            if self.store.rotations != rot0:
                self._rot_ms_est = 0.5 * self._rot_ms_est + 0.5 * (time.perf_counter() - tr) * 1000.0
            self._check_record()
            if self.record_blocked:
                self.c["record_blocked_steps"] += 1
            if self.fold is not None and self.fold.memory_estimate() / 1048576.0 > self.max_index_mb:
                self.state = A_DEGRADED
                self.last_error = "INDEX_CAP: %.1f MB > %.0f MB" % (self.fold.memory_estimate() / 1048576.0,
                                                                   self.max_index_mb)
                log.error("gölge danışman DEGRADED (bellek tavanı; karar ETKİLENMEZ): %s", self.last_error)
            elif self.fold is not None:
                self._maybe_snapshot(t0, budget)
            self._consec_errors = 0
        except Exception as exc:  # noqa: BLE001 — danışman ASLA toplayıcıyı durdurmaz
            self.errors += 1
            self._consec_errors += 1
            self.last_error = ("%s: %s" % (type(exc).__name__, exc))[:200]
            self.fold, self.reader, self._out = None, None, []
            self.mode = MODE_CATCH_UP
            log.warning("gölge danışman adımı başarısız (%d/%d; karar ETKİLENMEZ): %s", self._consec_errors,
                        BREAKER_ERRORS, exc)
            if self._consec_errors >= BREAKER_ERRORS:
                self.state = A_BREAKER
        ms = round((time.perf_counter() - t0) * 1000.0, 3)
        self._step_ms.append(ms)
        if ms > OVERRUN_FACTOR * budget:
            self._consec_overruns += 1
            if self._consec_overruns >= BREAKER_OVERRUNS:
                self.state = A_BREAKER
        else:
            self._consec_overruns = 0
        if self.state == A_BREAKER:
            log.error("gölge danışman DEVRE KESİCİ ile kapandı (süreç boyunca; karar ETKİLENMEZ): %s", self.last_error)
        self._write_status()
        return {"ms": ms, "state": self.state, "mode": self.mode, "advised": self.advised_last, "late": self.late_last}

    def _pct(self, p: float) -> float | None:
        xs = sorted(self._step_ms)
        if not xs:
            return None
        return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]

    def lag_rows(self) -> int | None:
        if self.mode == MODE_LIVE:
            return 0
        if self.fold is None or self._main is None:
            return None
        try:
            tot = int((XR.manifest(self.root).get("totals") or {}).get("records") or 0)
            return max(0, tot + int(getattr(self._main, "_line_count", 0) or 0) - int(self.fold.counters["rows"]))
        except Exception:  # noqa: BLE001
            return None

    def public_state(self) -> str:
        """Görünür durum: kesici / DEGRADED önce; iç durum OK iken bekleyen segment ya da kayıt engeli."""
        if self.state != A_OK:
            return self.state
        if self.waiting_segment is not None:
            return S_WAITING_SEGMENTS
        if self.record_blocked:
            return S_RECORD_BLOCKED
        return A_OK

    def health(self) -> dict[str, Any]:
        """`health.json["shared_experience"]["advisor"]` — O(1), ASLA istisna atmaz. Danışmanın adım süresi YALNIZ burada
        ve `advisor_status.json`da (toplayıcının `step_ms`i onu içermez)."""
        try:
            return {"state": self.public_state(), "advisor_sha": ADVISOR_SHA, "mode": self.mode,
                    "lag_rows": 0 if self.mode == MODE_LIVE else None,
                    "index_mb": round(self.fold.memory_estimate() / 1048576.0, 3) if self.fold is not None else None,
                    "advised_last": int(self.advised_last), "late_last": int(self.late_last), "errors": int(self.errors),
                    "breaker": self.state == A_BREAKER,
                    "step_ms": (self._step_ms[-1] if self._step_ms else None), "step_ms_p95": self._pct(0.95),
                    "record_blocked": self.record_blocked, "advice_emitted": int(self.c["advice_emitted"]),
                    "advice_written": int(self.c["advice_written"]),
                    "waiting_segment": dict(self.waiting_segment) if self.waiting_segment else None,
                    "segments_bad": int(self.c["segments_bad"]), "lines_bad": int(self.c["lines_bad"])}
        except Exception:  # noqa: BLE001
            return {"state": "UNKNOWN", "advisor_sha": ADVISOR_SHA}

    def status_doc(self) -> dict[str, Any]:
        from .advisor_eval import running_from_tallies
        fold = self.fold
        tallies = fold.tallies() if fold is not None else {}
        try:
            store = self.store.summary()
        except Exception as exc:  # noqa: BLE001
            store = {"error": str(exc)[:200]}
        warnings: list[str] = []
        if self._snap_deferred_consec >= SNAP_FORCE_AFTER:
            warnings.append("SNAPSHOT_DEFERRED_%d_STEPS" % self._snap_deferred_consec)
        if self.waiting_segment is not None:
            warnings.append("WAITING_SEGMENT_SEQ_%s" % self.waiting_segment.get("seq"))
        if self.record_blocked:
            warnings.append("RECORD_BLOCKED_%s" % self.record_blocked)
        return {"schema": STATUS_SCHEMA, "advisor_id": ADVISOR_ID, "advisor_sha": ADVISOR_SHA,
                "situation_schema_sha": SCHEMA_SHA, "banner": BANNER_TR, "state": self.public_state(),
                "internal_state": self.state, "mode": self.mode, "warnings": warnings,
                "record": {"blocked": self.record_blocked, "emitted": int(self.c["advice_emitted"]),
                           "written": int(self.c["advice_written"]), "retry_pending": len(self._retry),
                           "dropped": int(self.c["advice_dropped"]), "skipped_hwm": int(self.c["advice_skipped_hwm"]),
                           "hwm_target_seq": int(self._adv_hwm)},
                "segments": {"waiting": dict(self.waiting_segment) if self.waiting_segment else None,
                             "segments_bad": int(self.c["segments_bad"]), "lines_bad": int(self.c["lines_bad"])},
                "snapshot": {"deferred_consecutive": int(self._snap_deferred_consec),
                             "estimate_ms": round(self._snap_ms_est, 1)},
                "effect": "NONE", "last_step_at": self.last_step_at, "steps": self.steps,
                "step_ms_last": (self._step_ms[-1] if self._step_ms else None), "step_ms_p50": self._pct(0.5),
                "step_ms_p95": self._pct(0.95), "advised_last": self.advised_last, "late_last": self.late_last,
                "advisor_born_ms": self.born_ms, "advisor_born_at": XR._iso_ms(self.born_ms),
                "position": {"last_row_id": (self._pos_row if self.mode == MODE_LIVE or self.reader is None
                                             else self.reader.last_row_id),
                             "batch_clock_ms": fold.batch_clock if fold is not None else None,
                             "batch_clock_at": XR._iso_ms(fold.batch_clock) if fold is not None else None,
                             "lag_rows": self.lag_rows()},
                "index_mb": round(fold.memory_estimate() / 1048576.0, 3) if fold is not None else None,
                "max_index_mb": self.max_index_mb, "fold": fold.stats() if fold is not None else None,
                "counters": {**(dict(fold.counters) if fold is not None else {}), **self.c},
                "breaker": {"consecutive_errors": self._consec_errors, "consecutive_overruns": self._consec_overruns,
                            "tripped": self.state == A_BREAKER, "errors_limit": BREAKER_ERRORS,
                            "overruns_limit": BREAKER_OVERRUNS, "overrun_factor": OVERRUN_FACTOR},
                "errors": self.errors, "last_error": self.last_error,
                "budget": {"advisor_budget_ms": self.budget_ms, "advisor_catch_up_budget_ms": self.catch_up_budget_ms,
                           "advisor_rebuild_rows_per_step": self.rebuild_rows,
                           "advisor_snapshot_every_steps": self.snap_every},
                "ring_24h": fold.ring_24h() if fold is not None else {}, "tallies": tallies,
                "running": running_from_tallies(tallies), "books": list(BOOK_KEYS), "labels": list(LABELS),
                "advice_store": store}

    def _write_status(self) -> None:
        try:
            doc = self.status_doc()
            txt = json.dumps(doc, ensure_ascii=False, indent=1, default=str)
            if len(txt.encode("utf-8")) > STATUS_MAX_BYTES:
                doc["tallies"] = {}
                doc["fold"] = None
                txt = json.dumps(doc, ensure_ascii=False, indent=1, default=str)
            atomic_write_text(self.dir / STATUS_FILE, txt)
        except Exception as exc:  # noqa: BLE001
            self.c["status_errors"] += 1
            self.last_error = ("STATUS_WRITE: %s" % exc)[:200]


__all__ = ["A_BREAKER", "A_DEGRADED", "A_OK", "BANNER_TR", "BREAKER_ERRORS", "BREAKER_OVERRUNS", "LiveAdvisor",
           "META_FILE", "MODE_CATCH_UP", "MODE_LIVE", "OVERRUN_FACTOR", "SNAP_FORCE_AFTER", "STATE_FILE", "STATUS_FILE",
           "STATUS_SCHEMA", "SUMMARY_FILE", "S_RECORD_BLOCKED", "S_WAITING_SEGMENTS", "StoreReader"]
