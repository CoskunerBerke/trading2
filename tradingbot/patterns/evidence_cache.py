"""PATTERN KANITI ÖNBELLEĞİ + YAYIM SONRASI ÖN ISITMA (2026-10-01).

Sorun: arka plan yenileyicisi her yeni indeksi yayımladığında sürüm artar ve kanıt önbelleğinin anahtarı
(sembol, sürüm, son bar) değişir. Bir sonraki tur bütün indeks sembolleri × iki yön kNN sorgusunu ANA iş
parçacığında, coin head girdilerini kurarken yeniden hesaplar (kodun kendi notu: 138.891 olayda sorgu başına 12,6 sn).

Çözüm: yayım ANI DEĞİŞMEZ. Yenileyici paketi bugünkü noktada yayımlar ve ancak SONRA bu modüle haber verir; tek bir
arka plan işçisi YAYIMLANMIŞ paketin motoruyla, turun kullandığı fonksiyonla ve aynı girdilerle kanıtı hesaplayıp o
sürümün anahtarıyla önbelleğe koyar. Tur coin head aşamasına geldiğinde kanıt hazırdır.

Neden yayımdan ÖNCE değil: ön ısıtma dakikalar sürer. Yayımı o kadar bekletmek, aradaki turların ESKİ indeksle karar
vermesi demektir; hangi turun hangi indeks sürümünü gördüğü değişir. Bu karar-nötr DEĞİLDİR (aynı nedenle
`refresh_max_requests` artırımı da sahip onayı ister).

Değişmezler (testlerle kilitli):

1. Anahtar (sembol, sürüm, son bar). Tur ve ön ısıtma motoru ve sürümü AYNI paketten, tek okumayla alır; bir sürüm
   için hesaplanmış kanıt başka bir sürümün anahtarına ASLA konmaz, başka sürümü okuyan tura ASLA verilmez.
2. Tek hesaplama kilidi: indeks motoru üzerinde aynı anda EN FAZLA bir sorgu çalışır (tur ya da ön ısıtma). Tur,
   ön ısıtmanın o an hesapladığı sembolü isterse bekler ve AYNI sonucu alır; yeniden hesaplamaz.
3. Bir sembolün LONG+SHORT kanıtı tek birim olarak konur; yarım/karışık sonuç görünmez. İlk konan kalır.
4. Ön ısıtma daha yeni bir yayım görürse eski sürümü sembol sınırında bırakır ve eski pakete referans TUTMAZ:
   indeksin ek kopyası oluşmaz (sorgu kendi geçici dizileri dışında bellek ayırmaz).
5. Ön ısıtmanın her arızası yalnız loglanır; tur bugünkü gibi kendisi hesaplar.

ALT SÜREÇ (2026-10-05, `evidence_child`): sorgular worker sürecinin İÇİNDE koşunca GIL'i turdan, Box zamanlayıcısından
ve koruyucu izleyiciden çalıyordu (ölçüm ve mekanizma: `evidence_child` başlığı, docs/TOUR_CONTENTION_V1.md). Artık
`request_prewarm(..., use_child=True)` ile gelen iş, ilk gerçekten hesaplanacak sembolde yayımlanmış paketin motoruyla
`fork` edilen TEK bir alt süreçte hesaplanır; alt süreç iş süresince yaşar ve o sürede turun ıskaları da ona gider
(`compute`). Yukarıdaki değişmezler AYNEN geçerlidir:

* (1) Alt süreç yalnız kendisi için kurulduğu motorun sorgusunu yapar (`serves`: kimlik); başka motoru okuyan tur
  süreç içi hesaplar.
* (2) Alt süreçle her konuşma `compute_lock` ALTINDADIR: aynı anda en fazla bir sorgu, uçuştaki sembolü isteyen tur
  bekler ve aynı girdiyi alır. Bekleyen iş parçacığı boruda uyur, GIL tutmaz.
* (3) LONG+SHORT tek iletiyle gelir ve tek birim olarak konur.
* (4) İş bitince alt süreç KAPATILIR; daha yeni yayımda yayım ANINDA öldürülür (`note_published` /
  `request_prewarm` → `_retire_stale_child`; sembol sınırı beklenmez). Ebeveyn motorlara yalnız zayıf referans tutar.
  Bellek: aşağıda "BELLEK".
* (5) Alt süreç arızası (başlamama, ölüm, zaman aşımı) loglanır; o işin kalanı ve tur bugünkü süreç içi yolla
  hesaplanır. Alt süreçte bir sembolün hesabı istisna verirse tur o sembolü süreç içi yeniden hesaplar (bugünkü
  istisna/sonuç birebir), ön ısıtma yalnız loglar. İKİ İSTİSNA bu "yalnız loglanır" kuralının dışındadır (ayrıntı
  `evidence_child` başlığı): (a) fork'un kendisi asılırsa deadman worker'ı SONLANDIRIR (systemd yeniden başlatır) —
  süresiz donmanın yerine; (b) systemd'nin varsayılan `OOMPolicy=stop`'unda alt sürecin OOM'u bütün birimi durdurur
  (bugünkü bir OOM ile aynı; bellek koruması alt süreci OOM'dan ÖNCE öldürür — aşağıda "BELLEK"). (a)'dan sonra işaret
  dosyası (`fork_marker`) alt süreci o makinede kapalı tutar.

BELLEK (düzeltme turu 3, denetim bulgusu; ölçümler docs/TOUR_CONTENTION_V1.md §6): alt süreç yaşarken yazınca-kopyala
sayfalar (ölçülen ≈ indeksin %48–64'ü) iki süreçte ayrı durur. Ayrıca fork anında ebeveynde canlı olan ESKİ bir indeks
(yayım turun uçuştaki kanıt çağrısına denk geldi) ebeveynde sonradan bırakılınca sayfaları alt süreçte kalırdı (≈ bir
indeks boyutu, işin sonuna dek). Önlem: ebeveynin gördüğü motorlar zayıf referansla bilinir (`track_engine`); fork anında
canlı olan eskiler alt sürecin ölüm kancalarına verilir; biri serbest kalınca alt süreç öldürülür ve `_child_for` aynı
motor için YENİDEN fork eder (iş başına en çok `MAX_REFORKS`), uçuştaki sembol yeni alt süreçte yeniden istenir. Bellek
payı daralırsa (`evidence_child` "BELLEK KORUMASI") alt süreç kurulmaz ya da öldürülür ve işin kalanı süreç içi sürer
(uyarı). Bu öldürmelerin hiçbiri arıza sayılmaz ve kanıtı değiştirmez: her yolda aynı fonksiyon + aynı motor.

Geri dönüş: `history.evidence_subprocess: false` → `use_child` hiç verilmez, alt süreç kurulmaz, ön ısıtma bugünkü
kodun yolunu birebir izler (aynı kilitler, aynı log satırı).

KAPANIŞ (düzeltme turu 2): `stop()` KALICIDIR — `watch` kapanışında çağrılır (`TradingEngineV3.stop_pattern_evidence`),
alt süreci öldürür, işçiyi en çok `timeout` bekler; sonra gelen yayım (`request_prewarm`) işçiyi yeniden BAŞLATMAZ
(kapanırken yeni fork yok). Süreç çıkarken (`evidence_child.exiting()`; atexit kancası alt süreci öldürmüştür) öldürülen
alt süreç arıza sayılmaz, iş sessizce bırakılır ve süreç içi yola düşülmez (çıkışı GIL için yarışarak geciktirmesin).
Tur ıskası her durumda bugünkü gibi hesaplanır (alt süreç yoksa süreç içi).
"""
from __future__ import annotations

import logging
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .evidence_child import (
    RETIRE_MEMORY,
    RETIRE_RETAINED,
    RETIRE_SUPERSEDED,
    EvidenceChild,
    EvidenceChildComputeError,
    EvidenceChildError,
    EvidenceChildMemoryError,
    EvidenceChildRetainedError,
    exiting,
    fork_hung_before,
)

log = logging.getLogger(__name__)

Key = tuple  # (symbol, version, last_ts)


class _Stopping(Exception):
    """Kapanışta (`stop()`) öldürülen alt süreç: ön ısıtma işi sessizce bırakılır (arıza değil)."""


class _Superseded(Exception):
    """Daha yeni yayım ön ısıtmanın alt sürecini öldürdü: eski iş döngü başında bırakılır (arıza değil)."""


@dataclass
class EvidenceEntry:
    """Bir (sembol, sürüm, son bar) için kanıt. `written`: panel dosyası bu sürüm için yazıldı mı (yalnız tur yazar)."""
    ev: dict
    source: str = "tour"            # "tour" | "prewarm" — yalnız teşhis
    written: bool = False


class EvidenceCache:
    """İş parçacığı güvenli kanıt önbelleği + tek işçili ön ısıtma."""

    def __init__(self) -> None:
        self._lock = threading.Lock()               # sözlük ve iş kuyruğu
        self.compute_lock = threading.Lock()        # motor üzerinde TEK sorgu (tur ya da ön ısıtma)
        self._entries: dict[Key, EvidenceEntry] = {}
        self._job: tuple | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self.stats: dict[str, Any] = {"jobs": 0, "computed": 0, "already_cached": 0, "aborted": 0, "errors": 0,
                                      "last_version": None, "last_seconds": None, "last_error": "",
                                      # alt süreç (yalnız teşhis; karar girdisi DEĞİL)
                                      "child_started": 0, "child_computed": 0, "child_failures": 0,
                                      "child_in_process_fallbacks": 0, "child_private_mb_max": None,
                                      "child_last_error": "", "child_blocked": "",
                                      # bellek (düzeltme turu 3): yayımla öldürülen eski alt süreç, eski indeks
                                      # bırakılınca yeniden fork, bellek korumasıyla öldürme / fork reddi
                                      "child_superseded_kills": 0, "child_reforks": 0, "child_memory_kills": 0,
                                      "child_memory_refusals": 0}
        #: Canlı alt süreç (yalnız bir ön ısıtma işi süresince). YALNIZ `compute_lock` altında atanır/kullanılır/kapatılır;
        #: `stop()` kilitsiz yalnız öldürür.
        self._child: EvidenceChild | None = None
        #: Fork işaret dosyası (motor `state/` altında verir; None → işaretsiz, deadman yine korur). Bkz. `evidence_child`.
        self.fork_marker: Any = None
        #: Koşan alt süreçli ön ısıtma işinin kaydı (log satırı için): alt süreç pid'i, özel belleği, kim kaç sembol hesapladı.
        #: YALNIZ `compute_lock` altında ya da ön ısıtma işçisinde değişir.
        self._job_rec: dict | None = None
        self._blocked_logged = False
        #: `stop()` çağrıldı: kalıcı (işçi yeniden başlatılmaz). `_lock` altında yazılır.
        self._closed = False
        #: Ebeveynin gördüğü indeks motorlarının ZAYIF referansları (`track_engine`; `_lock` altında). Fork anında canlı
        #: olan eski motorlar alt sürecin ölüm kancalarına verilir (`EvidenceChild.start(watch=…)`).
        self._seen: list = []
        #: Koşan alt süreçli işin yeniden fork planı (motorun ZAYIF referansı, sürüm, fonksiyon, sayaç). YALNIZ
        #: `compute_lock` altında; ilk başarılı fork'ta kurulur, iş sonunda / yeni yayımda / bellek korumasında silinir.
        self._plan: dict | None = None

    #: Bir iş boyunca "eski indeks bırakıldı" nedeniyle en çok kaç kez yeniden fork edilir (sonra iş süreç içi sürer).
    MAX_REFORKS = 4
    #: Bir sembol için alt süreçte en çok kaç deneme (bilinçli öldürme + yeniden fork dahil); sonra süreç içi.
    CHILD_ATTEMPTS = 3

    # ------------------------------------------------------------------ önbellek
    def get(self, key: Key) -> EvidenceEntry | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: Key, ev: dict, *, source: str) -> EvidenceEntry:
        """İlk konan kalır (aynı anahtar için ikinci sonuç YOK SAYILIR)."""
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                e = self._entries[key] = EvidenceEntry(ev=ev, source=source)
            return e

    def prune_older(self, version: int) -> None:
        """Sürümü `version`dan eski girdiler erişilemez; bellekte de tutulmaz."""
        with self._lock:
            for k in [k for k in self._entries if k[1] < version]:
                self._entries.pop(k, None)

    def get_or_compute(self, key: Key, compute: Callable[[], dict]) -> EvidenceEntry:
        """Tur yolu: isabet → hemen; değilse hesaplama kilidini al, yeniden bak (ön ısıtma o arada koymuş olabilir), yoksa hesapla."""
        e = self.get(key)
        if e is not None:
            return e
        with self.compute_lock:
            e = self.get(key)
            if e is not None:
                return e
            ev = compute()
            return self.put(key, ev, source="tour")

    def track_engine(self, engine: Any) -> None:
        """Ebeveynin gördüğü bir indeks motorunu ZAYIF referansla kaydet (yalnız alt süreç yolu; ölüleri ayıkla). Fork
        anında hâlâ canlı olan eski motorlar alt sürecin ölüm kancalarına verilir: ebeveyn onu bırakınca alt süreç
        sayfalarını tutmasın diye öldürülüp yeniden kurulur (modül başlığı "BELLEK")."""
        if engine is None:
            return
        with self._lock:
            keep, found = [], False
            for r in self._seen:
                e = r()
                if e is None:
                    continue
                found = found or e is engine
                keep.append(r)
            e = None
            if not found:
                try:
                    keep.append(weakref.ref(engine))
                except TypeError:
                    pass
            self._seen = keep

    def note_published(self, engine: Any, version: Any = None) -> None:
        """Yayım anında (yenileyici iş parçacığı, alt süreç yolu): motoru kaydet ve başka motorun canlı alt sürecini
        hemen öldür (`_retire_stale_child`). Isıtılacak sembol olmasa da çağrılır; `request_prewarm` da aynısını yapar."""
        self.track_engine(engine)
        self._retire_stale_child(engine, version)

    def _watch_list(self, engine: Any) -> list:
        """Fork için: `engine` dışındaki canlı eski motorların ZAYIF referansları (güçlü referans dönmez)."""
        with self._lock:
            return [r for r in self._seen if r() is not None and r() is not engine]

    def compute(self, engine: Any, symbol: str, fn: Callable[[Any, str], dict], *, origin: str = "tour") -> dict:
        """`compute_lock` ALTINDA çağrılır. Bu motor için canlı alt süreç varsa kanıtı o hesaplar; yoksa ya da alt süreç
        arızalanırsa `fn(engine, symbol)` — bugünkü süreç içi yol, aynı fonksiyon, aynı motor.

        Alt süreçte hesap istisnası: tur (`origin="tour"`) süreç içi yeniden hesaplar (bugünkü istisna ya da sonuç
        birebir); ön ısıtma (`origin="prewarm"`) istisnayı yükseltir ve `_run` bugünkü gibi yalnız loglar.
        """
        return self._compute(engine, symbol, fn, origin=origin)[0]

    def _compute(self, engine: Any, symbol: str, fn: Callable[[Any, str], dict], *, origin: str) -> tuple[dict, bool]:
        """`compute` + kanıtın alt süreçte mi (True) süreç içinde mi (False) hesaplandığı (yalnız log kaydı için).

        Bilinçli öldürülen alt süreç (yeni yayım / eski indeks / bellek koruması; `EvidenceChild.retired`) arıza
        SAYILMAZ: "eski indeks bırakıldı" ise `_child_for` aynı motor için yeniden kurar ve sembol yeniden istenir;
        yeni yayımda ön ısıtma işi bırakılır (`_Superseded`), tur ve bellek korumasında süreç içi hesaplanır. Hangi
        yoldan gelirse gelsin kanıt aynı fonksiyon + aynı motorla hesaplanır (bit-aynı)."""
        self.track_engine(engine)
        tried = False
        for _attempt in range(self.CHILD_ATTEMPTS):
            child = self._child_for(engine)
            if child is None:
                break
            tried = True
            try:
                ev = child.compute(symbol)
                self.stats["child_computed"] += 1
                return ev, True
            except EvidenceChildComputeError as exc:
                if origin != "tour":
                    raise
                log.warning("pattern kanıtı alt süreçte hesaplanamadı (%s: %s); tur süreç içi hesaplıyor", symbol, exc)
                break
            except EvidenceChildError as exc:
                stopping = self._stopping()
                if child.retired and not stopping:
                    if child.retired == RETIRE_SUPERSEDED and origin != "tour":
                        raise _Superseded() from exc
                    continue                                # `_child_for` bırakır (gerekirse yeniden kurar)
                self._child_failed(exc, quiet=stopping)     # kapanışta öldürülen alt süreç arıza sayılmaz
                if stopping and origin != "tour":
                    raise _Stopping() from exc
                break
            finally:
                self._note_child_memory(child)
        if tried:
            self.stats["child_in_process_fallbacks"] += 1
        return fn(engine, symbol), False

    def _child_for(self, engine: Any) -> EvidenceChild | None:
        """`compute_lock` ALTINDA: `engine` için kullanılabilir alt süreç ya da None. Bilinçli öldürülmüş alt süreç
        burada bırakılır (`_drop_retired`; eski indeks nedeniyle öldürüldüyse iş sürüyorsa yeniden kurulur)."""
        c = self._child
        if c is not None and c.retired:
            self._drop_retired(c)
            c = self._child
        if c is None or not c.serves(engine):
            return None
        return c

    def _drop_retired(self, c: EvidenceChild) -> None:
        """`compute_lock` ALTINDA: bilinçli öldürülmüş alt süreci bırak (arıza değil).

        * "eski indeks bırakıldı": fork anında canlı olan eski motor ebeveynde serbest kaldı; alt süreç onun sayfalarını
          tutmasın diye öldürüldü. İş sürüyorsa AYNI motor için yeniden fork edilir (eski motor artık yoktur → yeni alt
          süreç onu içermez). İş başına en çok `MAX_REFORKS`; sonra iş süreç içi sürer.
        * "daha yeni yayım": yeniden kurulmaz (eski işin alt süreci).
        * "bellek koruması": yeniden kurulmaz; bu işin kalanı süreç içi (uyarı)."""
        reason = c.retired
        if self._child is c:
            self._child = None
        c.kill()
        self._note_child_memory(c)
        c.close()
        rec = self._job_rec
        if reason == RETIRE_RETAINED:
            plan = self._plan
            if plan is None or self._stopping():
                return
            if plan["reforks"] >= self.MAX_REFORKS:
                self._plan = None
                log.warning("pattern kanıtı alt süreci bu işte %d kez yeniden kuruldu; işin kalanı süreç içi",
                            plan["reforks"])
                return
            eng = plan["engine"]()
            if eng is None:
                self._plan = None
                return
            plan["reforks"] += 1
            self.stats["child_reforks"] += 1
            if rec is not None:
                rec["reforks"] = int(rec.get("reforks") or 0) + 1
            if self._fork_child(eng, plan["version"], plan["fn"]):
                # Bilgi satırı "pattern kanıtı alt süre…" ile BAŞLAMAZ: o önek sorun satırlarınındır (docs §9 ölçüt 3).
                log.info("pattern kanıtı: alt süreç yeniden kuruldu — fork anında canlı olan eski indeks ebeveynde "
                         "bırakıldı, alt süreç onu tutmasın (pid %s → %s)", c.pid, self._child.pid if self._child else "?")
            eng = None
            return
        self._plan = None
        if reason == RETIRE_MEMORY:
            self.stats["child_memory_kills"] += 1
            self.stats["child_last_error"] = (c.retired_detail or reason)[:300]
            if rec is not None:
                rec["memory"] = "bellek koruması"
            log.warning("pattern kanıtı alt süreci bellek koruması nedeniyle kapatıldı (süreç içi hesaplanıyor): %s",
                        c.retired_detail or reason)

    def _note_child_memory(self, child: EvidenceChild) -> None:
        """Alt sürecin o ana kadarki en yüksek özel belleğini işin kaydına ve istatistiğe yaz (yalnız teşhis)."""
        kb = child.private_kb_max
        if kb is None:
            return
        rec = self._job_rec
        if rec is not None and rec.get("pid") == child.pid:
            rec["kb"] = kb if rec.get("kb") is None else max(rec["kb"], kb)
        mb = round(kb / 1024.0, 1)
        prev = self.stats["child_private_mb_max"]
        self.stats["child_private_mb_max"] = mb if prev is None else max(prev, mb)

    def _start_child(self, bundle: Any, compute: Callable[[Any, str], dict]) -> bool:
        """`compute_lock` ALTINDA: yayımlanmış paketin motoru için alt süreci kur. Başarısızlık loglanır → False."""
        return self._fork_child(bundle.engine, int(getattr(bundle, "version", 0) or 0), compute)

    def _fork_child(self, engine: Any, version: int, compute: Callable[[Any, str], dict]) -> bool:
        """`compute_lock` ALTINDA: `engine` için alt süreci kur (ilk kez ya da eski indeks bırakılınca yeniden).

        Önceki bir worker alt süreci fork ederken asılıp sonlandırıldıysa (işaret dosyası) HİÇ denenmez: bu makinede
        fork asılma riski gerçekleşmiştir; sahip dosyayı silene kadar süreç içi yol (uyarı süreç başına bir kez).
        Bellek payı yetersizse fork yapılmaz (uyarı; süreç içi yol). Fork anında canlı olan eski motorlar alt sürecin
        ölüm kancalarına verilir (`_watch_list`)."""
        blocked = fork_hung_before(self.fork_marker)
        if blocked:
            self.stats["child_blocked"] = blocked[:300]
            if not self._blocked_logged:
                self._blocked_logged = True
                log.warning("pattern kanıtı alt süreci KAPALI (süreç içi hesaplanıyor): %s. Yeniden denemek için "
                            "dosyayı silin (worker yeniden başlatma gerekmez).", blocked)
            return False
        self.stats["child_blocked"] = ""
        if self._stopping():                        # kapanış: fork yok, uyarı yok (çağıran işi bırakır)
            return False
        old, self._child = self._child, None
        if old is not None:                         # savunma: bir anda en çok BİR alt süreç
            old.kill()
            old.close()
        try:
            try:
                child = EvidenceChild.start(engine, compute, version=int(version), marker=self.fork_marker,
                                            watch=self._watch_list(engine))
            except EvidenceChildRetainedError:     # eski motor el sıkışmasında öldü: bir kez daha (artık yok)
                self.stats["child_reforks"] += 1
                child = EvidenceChild.start(engine, compute, version=int(version), marker=self.fork_marker,
                                            watch=self._watch_list(engine))
        except EvidenceChildMemoryError as exc:
            self._plan = None
            self.stats["child_memory_refusals"] += 1
            self.stats["child_last_error"] = str(exc)[:300]
            if self._job_rec is not None:
                self._job_rec["memory"] = "bellek payı yetersiz"
            log.warning("pattern kanıtı alt süreci başlatılmadı — %s; süreç içi hesaplanıyor", exc)
            return False
        except EvidenceChildError as exc:
            self._plan = None
            if self._stopping():                    # çıkış kancası fork'u kapattı: arıza değil
                return False
            self.stats["child_failures"] += 1
            self.stats["child_last_error"] = str(exc)[:300]
            log.warning("pattern kanıtı alt süreci başlatılamadı (süreç içi hesaplanıyor): %s", exc)
            return False
        self._child = child
        if self._stopping():                        # `stop()` fork sırasında geldi ve alt süreci göremedi: kapat
            self._child = None
            child.kill()
            child.close()
            return False
        self.stats["child_started"] += 1
        if self._job_rec is not None:
            self._job_rec["pid"] = child.pid
        plan = self._plan
        if plan is None or plan["engine"]() is not engine:
            self._plan = {"engine": weakref.ref(engine), "version": int(version), "fn": compute, "reforks": 0}
        plan = None
        return True

    def _retire_stale_child(self, engine: Any, version: Any) -> None:
        """Yayım ANINDA (yenileyici iş parçacığı, kilitsiz — `stop()` gibi yalnız öldürür): canlı alt süreç başka bir
        motor içinse hemen öldür. Eski sürümün alt süreci sembol sınırını beklemez; yazınca-kopyala sayfaları ve
        ebeveyn bıraktıkça devraldığı eski indeks sayfaları hemen iade edilir. Uçuştaki tur isteği o sembolü süreç
        içi hesaplar (aynı kanıt); ön ısıtma eski işi bırakır. Kapatmayı (`close`) `compute_lock` sahibi yapar."""
        c = self._child
        if c is None or c.retired or engine is None or c.engine_is(engine):
            return
        c.retire(RETIRE_SUPERSEDED)
        self.stats["child_superseded_kills"] += 1
        log.info("pattern kanıtı: eski sürümün alt süreci (sürüm %d) daha yeni yayımla hemen kapatıldı (sürüm %s)",
                 c.version, version)

    def _child_failed(self, exc: Exception, *, quiet: bool = False) -> None:
        """`compute_lock` ALTINDA: arızalı alt süreci bırak (öldür + kapat), logla."""
        self._plan = None
        child, self._child = self._child, None
        if not quiet:
            self.stats["child_failures"] += 1
            self.stats["child_last_error"] = str(exc)[:300]
            log.warning("pattern kanıtı alt süreci arızalandı (süreç içi hesaplanıyor): %s", exc)
        if child is not None:
            child.kill()
            child.close()

    def _close_child(self) -> None:
        """İş sonunda alt süreci kapat (uçuştaki tur isteği bitene kadar `compute_lock` beklenir)."""
        with self.compute_lock:
            child, self._child = self._child, None
            self._plan = None
        if child is None:
            return
        self._note_child_memory(child)
        child.close()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def keys(self) -> list[Key]:
        with self._lock:
            return list(self._entries)

    # ------------------------------------------------------------------ ön ısıtma
    def request_prewarm(self, bundle: Any, keys: Iterable[Key], compute: Callable[[Any, str], dict],
                        is_current: Callable[[Any], bool], *, use_child: bool = False) -> None:
        """Yayım sonrası çağrılır (yenileyici iş parçacığı). HEMEN döner; iş tek arka plan işçisinde koşar.
        Bekleyen eski iş yenisiyle DEĞİŞTİRİLİR (yalnız en son yayım ısıtılır). `use_child`: sorgular alt süreçte
        (bkz. modül başlığı); False → bugünkü süreç içi yol."""
        job = (bundle, list(keys), compute, is_current, bool(use_child))
        with self._lock:
            if self._closed:                        # `stop()` sonrası (kapanış): yeni iş/fork yok; tur kendisi hesaplar
                log.debug("pattern kanıtı ön ısıtması kapalı (durduruldu); sürüm %s ısıtılmıyor",
                          getattr(bundle, "version", None))
                return
            self._job = job
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="pattern-evidence-prewarm", daemon=True)
                self._thread.start()
        engine = getattr(bundle, "engine", None)
        if use_child:
            self.track_engine(engine)
        self._retire_stale_child(engine, getattr(bundle, "version", None))     # iş kuyruğa girdikten SONRA

    def stop(self, timeout: float = 5.0) -> None:
        """KALICI durdurma (kapanış): yeni iş kabul edilmez, canlı alt süreç hemen öldürülür (bekleyen alım EOF görür;
        kapatmayı işçi yapar), işçi en çok `timeout` sn beklenir. O an süreç içi bir sembol hesaplıyorsa süre dolunca
        bırakılır — daemon iş parçacığıdır, süreçle biter."""
        with self._lock:
            self._closed = True
            self._job = None
        self._stop.set()
        self._wake.set()
        child = self._child                         # `_start_child` atamadan SONRA `_stopping()`'e bakar: kaçmaz
        if child is not None:
            child.kill()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=timeout)

    def _stopping(self) -> bool:
        """Kapanış: `stop()` çağrıldı ya da süreç çıkıyor (alt süreç çıkış kancasıyla öldürüldü)."""
        return self._stop.is_set() or exiting()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Testler/ölçüm için: bekleyen ve koşan iş bitene kadar bekle."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                busy = self._job is not None or self._wake.is_set() or self._running
            if not busy:
                return True
            time.sleep(0.01)
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                return
            with self._lock:
                job, self._job = self._job, None
                self._wake.clear()
                self._running = job is not None
            if job is None:
                continue
            try:
                self._run(*job)
            except Exception as exc:  # noqa: BLE001 — işçi ASLA ölmez; tur kendisi hesaplar
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("pattern kanıtı ön ısıtması başarısız (tur kendisi hesaplar): %s", exc)
            finally:
                job = None                          # eski pakete referans TUTULMAZ
                with self._lock:
                    self._running = False

    def _superseded(self, bundle: Any, is_current: Callable[[Any], bool]) -> bool:
        return self._stopping() or self._wake.is_set() or not is_current(bundle)

    def _run(self, bundle: Any, keys: list[Key], compute: Callable[[Any, str], dict],
             is_current: Callable[[Any], bool], use_child: bool = False) -> None:
        if not use_child:                           # geri dönüş anahtarı: bugünkü yol birebir (ek kilit/log yok)
            self._run_keys(bundle, keys, compute, is_current, False)
            return
        self._job_rec = {"pid": None, "kb": None, "child": 0, "inproc": 0, "reforks": 0, "memory": ""}
        try:
            self._run_keys(bundle, keys, compute, is_current, True)
        finally:
            self._close_child()                     # alt süreç yalnız iş süresince yaşar (bellek iade)
            self._job_rec = None

    def _run_keys(self, bundle: Any, keys: list[Key], compute: Callable[[Any, str], dict],
                  is_current: Callable[[Any], bool], use_child: bool) -> None:
        t0 = time.monotonic()
        version = int(getattr(bundle, "version", 0) or 0)
        self.stats["jobs"] += 1
        self.stats["last_version"] = version
        self.prune_older(version)
        done = cached = 0
        child_tried = False
        for key in keys:
            if self._superseded(bundle, is_current):
                self.stats["aborted"] += 1
                if self._stopping():
                    log.info("pattern kanıtı ön ısıtması kapanışta bırakıldı: sürüm %d (%d/%d sembol hazır)",
                             version, done + cached, len(keys))
                else:
                    log.info("pattern kanıtı ön ısıtması bırakıldı: sürüm %d yerine daha yeni yayım var (%d/%d sembol "
                             "hazır)", version, done + cached, len(keys))
                return
            if self.get(key) is not None:
                cached += 1
                continue
            with self.compute_lock:
                if self.get(key) is not None:      # tur o arada kendisi hesaplamış
                    cached += 1
                    continue
                if self._superseded(bundle, is_current):
                    continue                        # döngü başında bırakılır
                if use_child and not child_tried:
                    child_tried = True              # iş başına bir deneme; arızadan sonra iş süreç içi sürer
                    if not self._start_child(bundle, compute) and self._stopping():
                        continue                    # kapanış fork sırasında geldi: süreç içi hesaplanmaz, döngü başında bırakılır
                    if self._superseded(bundle, is_current):
                        continue                    # yayım fork sırasında geldi: döngü başında bırakılır, iş sonu kapatır
                try:
                    if use_child:
                        ev, in_child = self._compute(bundle.engine, key[0], compute, origin="prewarm")
                    else:
                        ev, in_child = compute(bundle.engine, key[0]), False
                except _Stopping:
                    self.stats["aborted"] += 1
                    return
                except _Superseded:
                    continue                        # yeni yayım alt süreci öldürdü: döngü başında bırakılır
                except Exception as exc:  # noqa: BLE001 — bu sembolü tur kendisi hesaplar
                    self.stats["errors"] += 1
                    self.stats["last_error"] = f"{key[0]}: {type(exc).__name__}: {exc}"[:300]
                    log.warning("pattern kanıtı ön ısıtılamadı (%s; tur kendisi hesaplar): %s", key[0], exc)
                    continue
                self.put(key, ev, source="prewarm")
                done += 1
                rec = self._job_rec
                if rec is not None:
                    rec["child" if in_child else "inproc"] += 1
        self.stats["computed"] += done
        self.stats["already_cached"] += cached
        self.stats["last_seconds"] = round(time.monotonic() - t0, 1)
        if not use_child:
            log.info("pattern kanıtı ön ısıtıldı: sürüm %d, %d sembol hesaplandı, %d zaten hazırdı, %.1f sn",
                     version, done, cached, self.stats["last_seconds"])
            return
        log.info("pattern kanıtı ön ısıtıldı: sürüm %d, %d sembol hesaplandı, %d zaten hazırdı, %.1f sn (%s)",
                 version, done, cached, self.stats["last_seconds"], self._job_where())

    def _job_where(self) -> str:
        """Alt süreçli işin log eki — işin KENDİ kaydından (alt süreç iş ortasında arızalansa da doğru)."""
        rec = self._job_rec or {}
        n_child, n_in = int(rec.get("child") or 0), int(rec.get("inproc") or 0)
        tail = ""
        if rec.get("reforks"):
            tail += f"; eski indeks için yeniden fork {int(rec['reforks'])}"
        if rec.get("memory"):
            tail += f"; {rec['memory']}"
        if rec.get("pid"):
            kb = rec.get("kb")
            mem = "?" if kb is None else str(round(kb / 1024.0))
            return (f"alt süreç pid {rec['pid']}, özel bellek {mem} MB; alt süreçte {n_child}, süreç içi {n_in} "
                    f"sembol{tail}")
        if n_in:
            return f"alt süreç kurulamadı; süreç içi {n_in} sembol{tail}"
        return "alt süreç gerekmedi"


__all__ = ["EvidenceCache", "EvidenceEntry"]
