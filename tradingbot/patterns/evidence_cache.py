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
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

Key = tuple  # (symbol, version, last_ts)


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
                                      "last_version": None, "last_seconds": None, "last_error": ""}

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

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def keys(self) -> list[Key]:
        with self._lock:
            return list(self._entries)

    # ------------------------------------------------------------------ ön ısıtma
    def request_prewarm(self, bundle: Any, keys: Iterable[Key], compute: Callable[[Any, str], dict],
                        is_current: Callable[[Any], bool]) -> None:
        """Yayım sonrası çağrılır (yenileyici iş parçacığı). HEMEN döner; iş tek arka plan işçisinde koşar.
        Bekleyen eski iş yenisiyle DEĞİŞTİRİLİR (yalnız en son yayım ısıtılır)."""
        job = (bundle, list(keys), compute, is_current)
        with self._lock:
            self._job = job
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="pattern-evidence-prewarm", daemon=True)
                self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

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
        return self._stop.is_set() or self._wake.is_set() or not is_current(bundle)

    def _run(self, bundle: Any, keys: list[Key], compute: Callable[[Any, str], dict],
             is_current: Callable[[Any], bool]) -> None:
        t0 = time.monotonic()
        version = int(getattr(bundle, "version", 0) or 0)
        self.stats["jobs"] += 1
        self.stats["last_version"] = version
        self.prune_older(version)
        done = cached = 0
        for key in keys:
            if self._superseded(bundle, is_current):
                self.stats["aborted"] += 1
                log.info("pattern kanıtı ön ısıtması bırakıldı: sürüm %d yerine daha yeni yayım var (%d/%d sembol hazır)",
                         version, done + cached, len(keys))
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
                try:
                    ev = compute(bundle.engine, key[0])
                except Exception as exc:  # noqa: BLE001 — bu sembolü tur kendisi hesaplar
                    self.stats["errors"] += 1
                    self.stats["last_error"] = f"{key[0]}: {type(exc).__name__}: {exc}"[:300]
                    log.warning("pattern kanıtı ön ısıtılamadı (%s; tur kendisi hesaplar): %s", key[0], exc)
                    continue
                self.put(key, ev, source="prewarm")
                done += 1
        self.stats["computed"] += done
        self.stats["already_cached"] += cached
        self.stats["last_seconds"] = round(time.monotonic() - t0, 1)
        log.info("pattern kanıtı ön ısıtıldı: sürüm %d, %d sembol hesaplandı, %d zaten hazırdı, %.1f sn",
                 version, done, cached, self.stats["last_seconds"])


__all__ = ["EvidenceCache", "EvidenceEntry"]
