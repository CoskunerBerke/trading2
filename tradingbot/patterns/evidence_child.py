"""PATTERN KANITI ALT SÜRECİ (2026-10-05) — kNN kanıt sorguları worker'ın GIL'ini PAYLAŞMAZ.

Sorun (VPS günlüğü, 2026-10-02..05): 4h kapanışından sonra yenileyici yeni indeksi yayımlayınca ön ısıtma işçisi
(`evidence_cache`) worker SÜRECİNİN İÇİNDE dakikalarca kNN sorgusu koşar. Sorgu döngüsü olay başına `np.corrcoef`
çağırır; bu çağrı GIL'i çok kısa aralıklarla bırakıp geri alır. CPython'da GIL bekleyen iş parçacığı ancak 5 ms boyunca
hiç el değiştirmezse "bırak" ister; sık bırak-al yapan bir iş parçacığı bu sayacı sürekli tazelediği için diğer iş
parçacıkları (tur, Box zamanlayıcısı, koruyucu izleyici) GIL'i ancak yarışı şans eseri kazanınca alır. Yerel ölçüm
(BOŞTAKİ 4 çekirdekli makine, aynı kod): sorgu koşarken 1 ms uyuyan iş parçacığının GIL'i geri alma gecikmesi p50 36 ms /
en çok 370 ms (sorgu yokken 0,1 ms; saf Python yükte 5 ms); turun numpy/pandas, soket ve dosya adımları 33–145 kat, saf
Python adımları ~10 kat yavaşladı. Aynı betik başka işlerle YÜKLÜ aynı makinede (yük ortalaması 6–7) süreç içi yol için
yalnız 1,0–2,7 kat verdi: açlığın şiddeti çekirdek sayısına ve makinenin yüküne güçlü biçimde bağlıdır. Alt süreç yolu
her koşuda ~1 kat. VPS'teki 43 dk'lık turların (semboller 544 sn, yürütme 856 sn) ve Box'ın kaçırdığı 5m mumun bu
mekanizmayla açıklanması bir HİPOTEZDİR (VPS 4 vCPU — sahibin aktardığı; yükü bilinmiyor); dağıtımdan sonra
docs/TOUR_CONTENTION_V1.md §9 ile doğrulanır.

Çözüm: sorgular `fork` ile ayrılan TEK bir alt süreçte koşar. Alt süreç yayımlanmış paketin motorunu kopyalamadan
(yazınca-kopyala sayfalar) görür; yani motor, fonksiyon (`TradingEngineV3._evidence_query`) ve girdiler turunkiyle AYNI
nesnelerdir, kanıt bit-bit aynıdır (pickle float'ı kayıpsız taşır; test kilitli). Ebeveyn yalnız boru üzerinden sonucu
bekler — beklerken GIL'i tutmaz. Alt süreç yalnız bir ön ısıtma işi süresince yaşar (iş bitince kapanır; daha yeni yayım
gelince HEMEN öldürülür) ve o süre içinde turun önbellek ıskaları da ona gider (`EvidenceCache.compute`).

Neden `fork` (spawn/forkserver değil): yeni süreç indeksi ya diskten yeniden kurmalı (40+ sn CPU, yayımlananla aynı
olduğunun kanıtı yok) ya da ebeveynden pickle ile almalıdır (152k olayda yüzlerce MB, ebeveynde GIL'i saniyelerce
tutan tek bir C çağrısı ve iş boyunca tam ikinci kopya). `fork` indeksin aynı bellek görüntüsünü bedelsiz verir.

Çok iş parçacıklı süreçte `fork` riskleri ve önlemler:

* FORK'UN KENDİSİ ASILABİLİR. CPython `fork()`'u GIL'i tutarak çağırır; libc önce yüklü kütüphanelerin fork öncesi
  kancalarını koşturur. OpenBLAS'ın kancası (`blas_thread_shutdown_`) kendi iş parçacığı havuzunu kapatıp `pthread_join`
  ile bekler; o anda BAŞKA bir iş parçacığı çok iş parçacıklı bir BLAS işi yürütüyorsa havuz iş parçacığı kapatma
  işaretini kaçırır ve join SONSUZA DEK bekler. Yerelde yeniden üretildi: arka planda 300×300 matris çarpımı koşarken
  her denemede birkaç saniye içinde asıldı. GIL fork eden iş parçacığında kaldığı için tur, Box zamanlayıcısı ve koruyucu
  izleyici de donar; bu anda hiçbir Python zaman aşımı (aşağıdakiler dahil) çalışamaz. Önlemler:

  - DEADMAN (`FORK_DEADMAN_S`): yalnız bu modülün fork'u süresince süreç zamanlayıcısı (ITIMER_REAL) kurulur
    (`os.register_at_fork` kancası, iş parçacığına özel bayrakla; başka fork'lara dokunmaz) ve fork dönünce söndürülür.
    Fork asılırsa SIGALRM'nin VARSAYILAN eylemi worker'ı sonlandırır — bunu çekirdek yapar, GIL gerekmez — ve systemd
    `Restart=on-failure` onu yeniden başlatır (günlükte `status=14/ALRM`). Süresiz donma yerine tek bir yeniden başlatma
    (ilk tur uzun sürer; Box mumu kaçabilir). SIGALRM başka bir amaçla kullanılıyorsa (işleyici kurulu, engelli ya da
    zamanlayıcı kurulu) fork HİÇ yapılmaz: bugünkü süreç içi yol.
  - İŞARET DOSYASI (`state/pattern_evidence_fork.marker`): fork'tan hemen önce yazılır, fork dönünce silinir. Yeniden
    başlayan worker dosyayı başka bir pid'le bulursa önceki worker fork'ta asılıp sonlandırılmıştır: alt süreç o makinede
    KAPALI kalır (her yayımda yeniden asılma / yeniden başlatma döngüsü olmaz) ve bir kez uyarı loglanır. Sahip dosyayı
    silince yeniden denenir.
  - DEĞİŞMEZ: worker'da hiçbir iş parçacığı çok iş parçacıklı seviye-3 BLAS (matris-matris çarpımı, `np.linalg`
    ayrıştırmaları) koşmaz. Bugünkü kodda yalnız vektör / matris-vektör çarpımı, küçük `corrcoef`/`cov` ve tamsayı
    çarpımı vardır; bağımsız denetimde matris-vektör yüküyle 2.700 fork'ta asılma görülmedi (risk gizli, sıfır değil).
    Yeni bir BLAS çağrısı `tests/test_evidence_subprocess_v1.py`'deki izin listesi testini kırar: bu risk
    değerlendirilmeden eklenemez. Risk yalnız OpenBLAS havuzu birden çok iş parçacıklıysa (çok çekirdekli makine)
    vardır; yenileyicinin başlangıç satırı sayıyı yazar (`blas_threads`).
  - ÖNERİ (bu işin kapsamı DIŞINDA): worker'a `OPENBLAS_NUM_THREADS=1` havuzu ve riski tamamen kaldırır; ama
    matris-vektör / nokta çarpımlarının toplama sırasını değiştirebilir (karar girdisi olan olasılıklarda son bit) →
    ayrı bir karar-nötrlük kanıtı ve sahip onayı ister.

* Fork'tan sonra alt süreç yalnız saf hesap yapar: loglamaz, uyarı basmaz (`warnings` susturulur), stdout/stderr
  /dev/null'a yönlenir — başka bir iş parçacığının `fork` anında tuttuğu akış/log kilidine hiç dokunmaz. İçe aktarma
  kilidini CPython `fork` sırasında yeniden kurar.
* Asılan ya da ölen ALT SÜREÇ ebeveyni bekletmez: hazır el sıkışması (`START_TIMEOUT_S`); istek sürerken alt süreç
  `STALL_TIMEOUT_S` boyunca HİÇ CPU harcamazsa (kilitlenme: hesap yapan süreç CPU harcar, kilitte bekleyen harcamaz)
  öldürülür; ayrıca istek başına üst sınır (`REQUEST_TIMEOUT_S`); boru kapanınca (ölüm) anında `EvidenceChildError`.
  Çağıran bugünkü süreç içi yola düşer.
* Alt süreç ebeveynden uzun yaşamaz: devraldığı EBEVEYN boru ucunu ilk iş olarak kapatır (ebeveyn ölünce boru EOF verir
  ve alt süreç çıkar); Linux'ta ayrıca `PR_SET_PDEATHSIG=SIGKILL` (aksi hâlde devraldığı tekil kilit dosyası
  tanımlayıcısı yeniden başlatılan worker'ı bekletebilirdi). SIGTERM'i YOK SAYAR: birim `KillMode=control-group` olsa
  bile worker son turunu alt süreçle bitirir; kapatmayı ebeveyn yapar (SIGKILL ve PDEATHSIG yine geçerlidir).
* KAPANIŞ alt süreci BEKLEMEZ (düzeltme turu 2, denetim bulgusu): alt süreç SIGTERM'i yok saydığı için
  `multiprocessing`'in çıkış kancası (`util._exit_function`: daemon alt sürece SIGTERM, sonra ZAMAN AŞIMSIZ `join`) bütün
  ön ısıtma işini (VPS'te 7–14 dk) beklerdi → `systemctl stop` 90 sn'yi aşıp SIGKILL/`timeout` olurdu. İki katman:
  `watch` kapanışı `EvidenceCache.stop()` ile alt süreci öldürür; her çıkış yolu için de ilk fork'ta, `multiprocessing.util`
  içe aktarıldıktan SONRA bir `atexit` kancası kaydedilir (`atexit` ters sırada koşar → onunkinden ÖNCE): kanca yeni fork'u
  kapatır (`exiting()`), canlı alt süreçleri SIGKILL ile öldürüp en çok `EXIT_REAP_S` bekler.
* DEVRALINAN TANIMLAYICILAR (düzeltme turu 2): `fork` ebeveynin bütün açık tanımlayıcılarını kopyalar (O_CLOEXEC exec
  olmadan işlemez): HTTP soketleri, tekil kilit dosyası, o an `subprocess` penceresindeki boru uçları (ör. kasa
  `git`'inin stdout yazma ucu: ebeveynin `communicate`'i alt süreç çıkana dek EOF görmezdi). Alt süreç ilk iş olarak
  0–2 ve kendi borusu dışındaki her tanımlayıcıyı `/dev/null`'un bir kopyasıyla DEĞİŞTİRİR (`dup2`: numara dolu kalır,
  devralınan nesnelerin sonradan yanlış dosyayı kapatması olmaz; ebeveynin dosya/soket/boru nesnelerine başvuru kalmaz).
  Sorgu hiçbir tanımlayıcı kullanmaz (salt bellek). Bu, `multiprocessing`'in bekçi borusunu da kapatır: ebeveyn alt
  sürecin bitişini bekçi borusundan değil `waitpid` ile yoklayarak bekler (`_reap`; `join(timeout)` kullanılmaz).
* BELLEK (düzeltme turu 3; ölçümler docs/TOUR_CONTENTION_V1.md §6). Alt sürecin özel belleği iki kaynaktan gelir:
  (a) YAZINCA-KOPYALA: alt süreç sorguda dokunduğu nesnelerin başvuru sayaçlarına yazar, o sayfalar kopyalanır —
  ölçülen ≈ indeks boyutunun %48–64'ü. Alt sürecin GC'si `gc.freeze()` ile ebeveynden gelen nesneleri dolaşmaz;
  ebeveyn de alt süreç yaşarken kendi GC'sini dondurur (`_freeze_parent_gc`; yoksa ebeveynin tam toplaması ortak
  sayfaları kopyalatır: 335 MB'lık süreçte +68 MB, dondurunca 0). (b) DEVRALINIP BIRAKILAN SAYFALAR (denetim bulgusu):
  fork anında ebeveynde canlı olan her nesnenin sayfaları alt süreçte de eşlenir; ebeveyn o nesneyi SONRA bırakırsa
  fiziksel sayfalar alt sürecin ÖZEL belleği olur. Yayım turun uçuştaki kanıt çağrısına denk gelirse fork anında ESKİ
  indeks canlıdır → önlem olmadan alt süreç işin sonuna dek koca bir eski indeks taşırdı (ölçüldü: 101 MB'lık indekste
  alt süreç USS 56 → 144 MB). Üç önlem:
  - Fork anında canlı olan eski motorlara ÖLÜM KANCASI (`start(watch=…)`, `_pin_callback`): eski motor ebeveynde
    serbest kalırken — içeriği bırakılmadan ÖNCE — alt süreç öldürülür (`RETIRE_RETAINED`); `EvidenceCache` aynı motor
    için yeniden fork eder (eski motor artık yok → yeni alt süreç onu içermez) ve uçuştaki sembolü yeniden ister.
  - Daha yeni yayımda eski sürümün alt süreci yayım ANINDA öldürülür (`RETIRE_SUPERSEDED`; sembol sınırı beklenmez).
  - BELLEK KORUMASI: fork ancak en dar bellek payı (`memory_headroom_mb`: cgroup sınırı − dosya önbelleği hariç
    kullanım, ya da sistem MemAvailable) `MEMORY_FORK_HEADROOM_MB` üstündeyse yapılır; alt süreç yaşarken pay
    `MEMORY_CHECK_S`'de bir okunur ve `MEMORY_KILL_HEADROOM_MB` altına inerse alt süreç öldürülür (`RETIRE_MEMORY`,
    uyarı) ve iş süreç içi sürer. Böylece alt süreç cgroup'u `MemoryMax`'a İTEMEZ: sınıra yaklaşılırsa ilk giden odur.
  Bilinçli öldürmeler arıza sayılmaz; kanıt her yolda aynı fonksiyon + aynı motorla hesaplanır (bit-aynı). Alt süreç
  `oom_score_adj=1000` (OOM'da çekirdek ÖNCE onu seçer) ve CPU'da turu itmesin diye `nice +10` alır. DİKKAT:
  systemd'nin varsayılan `OOMPolicy=stop`'u birimdeki HERHANGİ bir sürecin OOM ile öldürülmesinde bütün birimi durdurur
  (`Restart=on-failure` yeniden başlatır); bellek koruması bu yüzden OOM'dan ÖNCE davranır. "Alt süreç ölür, worker
  sürer" davranışı birime `OOMPolicy=continue` ister (işletim değişikliği; sahip onayı).
* Yalnız Linux (`supported`): başka platformda alt süreç hiç denenmez (sessizce bugünkü süreç içi yol). `fork`
  başarısızsa: hata loglanır, yol bugünkü süreç içi hesaptır.

Karar-nötrlük: yayım NOKTASI ve kuralı (yenileyicinin kodu ve iş parçacığı), turun sürümü okuma kuralı (tek okuma),
önbellek anahtarı ve kanıt DEĞİŞMEZ; bu modül yalnız sorgunun hangi süreçte koştuğunu değiştirir. Saat-duvarı zamanı
(turların ve yayımların ne zaman bittiği) bugün de yüke bağlıdır ve bu değişiklikle değişir — amaç budur; bkz.
docs/TOUR_CONTENTION_V1.md §5. Geri dönüş anahtarı: `history.evidence_subprocess: false` → bugünkü süreç içi yol (alt
süreç hiç kurulmaz). Anahtar iki değerinde de karar-nötr olduğu için karar kimliğine (`TradingEngineV3.config_hash`)
girmez.
"""
from __future__ import annotations

import atexit
import contextlib
import gc
import os
import signal
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable

#: Fork işaret dosyasının adı (worker'ın `state/` dizininde; bkz. modül başlığı "İŞARET DOSYASI").
MARKER_NAME = "pattern_evidence_fork.marker"

#: Alt sürecin BİLİNÇLİ olarak öldürülme nedenleri (`EvidenceChild.retired`; arıza DEĞİL, arıza sayacı yok — bellek
#: koruması uyarı yazar). Bkz. modül başlığı "BELLEK".
RETIRE_SUPERSEDED = "daha yeni yayım"          # eski sürümün alt süreci: yeni yayım ANINDA öldürülür
RETIRE_RETAINED = "eski indeks bırakıldı"       # fork anında canlı olan eski motor ebeveynde serbest kaldı → yeniden fork
RETIRE_MEMORY = "bellek koruması"               # cgroup/sistem bellek payı eşiğin altına indi → iş süreç içi sürer


class EvidenceChildError(RuntimeError):
    """Alt süreç kullanılamaz: başlamadı, öldü, zaman aşımı ya da protokol hatası. Çağıran süreç içi yola düşer."""


class EvidenceChildMemoryError(EvidenceChildError):
    """Bellek payı fork için yetersiz: alt süreç HİÇ kurulmadı (çağıran süreç içi yola düşer)."""


class EvidenceChildRetainedError(EvidenceChildError):
    """El sıkışması sürerken fork anında canlı olan eski motor serbest kaldı ve alt süreç öldürüldü (arıza değil):
    çağıran hemen yeniden fork edebilir — eski motor artık yoktur."""


class EvidenceChildComputeError(RuntimeError):
    """Alt süreç sağlam; sembolün kanıt hesabı alt süreçte istisna verdi (metin taşınır)."""


def supported() -> bool:
    """Alt süreç yolu yalnız Linux'ta (fork + /proc + prctl). Başka platformda çağıran sessizce süreç içi hesaplar."""
    return sys.platform.startswith("linux")


# ------------------------------------------------------------------ fork koruması (deadman)
#: Yalnız bu modülün fork'u süresince, YALNIZ fork eden iş parçacığında dolu (saniye). `os.fork` kancaları buna bakar;
#: başka kodun fork'una (bugün worker'da yok) dokunulmaz.
_FORK_GUARD = threading.local()
_DEADMAN_LOCK = threading.Lock()


def _deadman_before_fork() -> None:
    """`os.fork` öncesi kanca: GIL tutulurken, C kütüphanelerinin fork öncesi kancalarından (OpenBLAS) ÖNCE koşar."""
    s = getattr(_FORK_GUARD, "seconds", 0.0)
    if s:
        signal.setitimer(signal.ITIMER_REAL, s)


def _deadman_after_fork_parent() -> None:
    """Fork döndü (başarılı ya da başarısız): zamanlayıcıyı söndür."""
    if getattr(_FORK_GUARD, "seconds", 0.0):
        signal.setitimer(signal.ITIMER_REAL, 0)


def _deadman_after_fork_child() -> None:
    """Alt süreç: zamanlayıcı fork'ta ALT SÜRECE GEÇMEZ (çekirdek sıfırlar); bayrak da temizlenir."""
    _FORK_GUARD.seconds = 0.0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_deadman_before_fork, after_in_parent=_deadman_after_fork_parent,
                        after_in_child=_deadman_after_fork_child)


def deadman_unavailable() -> str | None:
    """Deadman kurulamıyorsa nedeni (o zaman fork YAPILMAZ: süreç içi yol); kurulabiliyorsa None.

    SIGALRM'nin eylemi VARSAYILAN (süreci sonlandır) olmalı, engellenmemeli ve ITIMER_REAL başka biri tarafından kurulu
    olmamalı — aksi hâlde asılı fork'u ne sonlandırabiliriz ne de başkasının zamanlayıcısını bozmadan kurabiliriz."""
    if not hasattr(signal, "setitimer") or not hasattr(os, "register_at_fork"):
        return "platform ITIMER_REAL / register_at_fork vermiyor"
    try:
        if signal.getsignal(signal.SIGALRM) != signal.SIG_DFL:
            return "SIGALRM'nin işleyicisi var (varsayılan eylem worker'ı sonlandırmaz)"
        if signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, []):
            return "SIGALRM engelli"
        if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
            return "ITIMER_REAL başka bir amaçla kurulu"
    except (OSError, ValueError, AttributeError) as exc:
        return f"SIGALRM denetlenemedi ({type(exc).__name__})"
    return None


@contextlib.contextmanager
def _fork_deadman(seconds: float):
    """Gövdedeki fork `seconds` içinde dönmezse SIGALRM (varsayılan eylem) worker'ı sonlandırır. Kurulamazsa
    `EvidenceChildError` (fork yapılmaz)."""
    with _DEADMAN_LOCK:
        why = deadman_unavailable()
        if why:
            raise EvidenceChildError(f"fork koruması kurulamadı ({why}); fork yapılmadı")
        _FORK_GUARD.seconds = float(seconds)
        try:
            yield
        finally:
            _FORK_GUARD.seconds = 0.0
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)     # fork hiç olmadıysa da kalıntı bırakma (yukarıda boştu)
            except (OSError, ValueError):
                pass


# ------------------------------------------------------------------ fork işaret dosyası
def _self_identity() -> str:
    """Bu sürecin kimliği: pid + çekirdeğin verdiği başlangıç zamanı (pid yeniden kullanılsa da ayırt eder)."""
    try:
        with open("/proc/self/stat", encoding="ascii") as fh:
            start = fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        start = "0"
    return f"{os.getpid()}:{start}"


def _mark(path: Path | None) -> bool:
    """Fork'tan hemen önce: "bu süreç şu an fork ediyor". Yazılamazsa False (deadman yine korur)."""
    if path is None:
        return False
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(f"{_self_identity()} {int(time.time())}\n")
        return True
    except OSError:
        return False


def _unmark(path: Path | None) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)


def fork_hung_before(path: Path | None) -> str | None:
    """Önceki bir worker alt süreci fork ederken sonlandırıldıysa (işaret dosyası BAŞKA bir sürecin kimliğiyle duruyor)
    açıklama; değilse None. Bu sürecin kendi kalıntısı (olmamalı) sessizce silinir. Dosya okunamıyorsa fail-safe:
    açıklama (alt süreç kapalı kalır)."""
    if path is None:
        return None
    try:
        parts = Path(path).read_text(encoding="ascii", errors="replace").split()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"fork işaret dosyası okunamadı ({type(exc).__name__}: {path})"
    ident = parts[0] if parts else "?"
    if ident == _self_identity():
        _unmark(path)
        return None
    at = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(at)) if at is not None else "?"
    return (f"önceki worker (pid {ident.split(':')[0]}) {when} anında pattern kanıtı alt sürecini fork ederken "
            f"sonlandı (fork asılması → deadman); işaret dosyası: {path}")


# ------------------------------------------------------------------ teşhis
def blas_threads() -> int | None:
    """numpy'nin yüklediği OpenBLAS'ın iş parçacığı sayısı (yalnız teşhis). 1 → havuz yok, fork asılma riski yok;
    bilinmiyorsa (OpenBLAS değil, Linux değil) None. EBEVEYNDE çağrılır; yüklü kütüphaneyi `RTLD_NOLOAD` ile bulur,
    hiçbir şey yüklemez ve hiçbir ayarı değiştirmez."""
    if not supported():
        return None
    try:
        import ctypes

        import numpy  # noqa: F401 — kütüphane yüklü olsun (worker'da zaten yüklü)
        paths: list[str] = []
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split(maxsplit=5)
                if len(parts) == 6:
                    p = parts[5].strip()
                    if "openblas" in os.path.basename(p).lower() and p not in paths:
                        paths.append(p)
        for p in paths:
            lib = ctypes.CDLL(p, mode=getattr(os, "RTLD_NOLOAD", 0))
            for name in ("scipy_openblas_get_num_threads64_", "scipy_openblas_get_num_threads",
                         "openblas_get_num_threads64_", "openblas_get_num_threads"):
                fn = getattr(lib, name, None)
                if fn is not None:
                    fn.restype = ctypes.c_int
                    return int(fn())
    except Exception:  # noqa: BLE001 — yalnız teşhis
        return None
    return None


# ------------------------------------------------------------------ bellek payı (cgroup + sistem)
_CG_UNLIMITED = 1 << 60                          # cgroup v1'in "sınır yok" değeri (≈ 2^63, sayfaya yuvarlanmış) bunun üstü


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _stat_kv(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _cgroup_headrooms(proc_cgroup: str | None, sys_root: str = "/sys/fs/cgroup") -> list[tuple[int, str]]:
    """Bu sürecin cgroup'unda ve atalarında SINIRI OLAN her düzey için (pay bayt, ad).

    pay = sınır − (kullanım − dosya önbelleği). Dosya önbelleği (active_file + inactive_file) çıkarılır: çekirdek onu
    OOM'dan ÖNCE geri alır; yedekleme gibi büyük dosya işleri sayacı şişirir ama OOM getirmez. cgroup v2
    (`memory.max`/`memory.current`/`memory.stat`) ve v1 (`memory.limit_in_bytes`/`memory.usage_in_bytes`/
    `total_*_file`) okunur. Okunamayan ya da sınırsız düzey atlanır."""
    out: list[tuple[int, str]] = []
    for line in (proc_cgroup or "").splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) != 3:
            continue
        hid, ctrls, path = parts
        if hid == "0" and ctrls == "":
            base, v2 = sys_root, True
        elif "memory" in ctrls.split(","):
            base, v2 = os.path.join(sys_root, "memory"), False
        else:
            continue
        rel = path.strip("/")
        while True:
            d = os.path.join(base, rel) if rel else base
            if v2:
                lim_s = (_read_text(os.path.join(d, "memory.max")) or "").strip()
                cur_s = (_read_text(os.path.join(d, "memory.current")) or "").strip()
                lim = int(lim_s) if lim_s.isdigit() else None
                st = _stat_kv(_read_text(os.path.join(d, "memory.stat"))) if lim is not None else {}
                files = st.get("active_file", 0) + st.get("inactive_file", 0)
            else:
                lim_s = (_read_text(os.path.join(d, "memory.limit_in_bytes")) or "").strip()
                cur_s = (_read_text(os.path.join(d, "memory.usage_in_bytes")) or "").strip()
                lim = int(lim_s) if lim_s.isdigit() and int(lim_s) < _CG_UNLIMITED else None
                st = _stat_kv(_read_text(os.path.join(d, "memory.stat"))) if lim is not None else {}
                files = (st.get("total_active_file", st.get("active_file", 0))
                         + st.get("total_inactive_file", st.get("inactive_file", 0)))
            if lim is not None and cur_s.isdigit():
                used = max(0, int(cur_s) - files)
                out.append((lim - used, f"cgroup {'/' + rel if rel else '/'}"))
            if not rel:
                break
            rel = os.path.dirname(rel)
    return out


def memory_headroom_mb() -> tuple[float, str] | None:
    """En dar bellek payı (MB) ve kaynağı: bu sürecin cgroup zincirindeki sınırlar (`_cgroup_headrooms`) ve sistemin
    `MemAvailable`'ı (takas yok: sistem OOM'u da birimi durdurur). Hiçbiri okunamazsa None (koruma devre dışı kalır;
    yalnız /proc'suz ortamda). Ucuzdur (birkaç küçük dosya); alt süreç yaşarken saniyede bir okunur."""
    cands = _cgroup_headrooms(_read_text("/proc/self/cgroup"))
    for line in (_read_text("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            try:
                cands.append((int(line.split()[1]) * 1024, "sistem MemAvailable"))
            except (IndexError, ValueError):
                pass
            break
    if not cands:
        return None
    b, where = min(cands)
    return b / (1024.0 * 1024.0), where


def _headroom_or_none() -> tuple[float, str] | None:
    """`memory_headroom_mb`, ama hiçbir koşulda istisna atmaz (okuma hatası → None = koruma o an devre dışı)."""
    try:
        return memory_headroom_mb()
    except Exception:  # noqa: BLE001 — teşhis okuması kanıt hesabını bozmaz
        return None


# ------------------------------------------------------------------ ebeveyn GC dondurma (iç içe güvenli)
_FREEZE_LOCK = threading.Lock()
_FREEZE_DEPTH = 0


def _freeze_parent_gc() -> None:
    """İlk canlı alt süreçte ebeveynin GC'sini dondur: o anki nesneler toplamalarda dolaşılmaz (ortak sayfalar
    kopyalanmaz). Yalnız GC zamanlamasını etkiler; hiçbir değeri değiştirmez."""
    global _FREEZE_DEPTH
    with _FREEZE_LOCK:
        if _FREEZE_DEPTH == 0:
            gc.freeze()
        _FREEZE_DEPTH += 1


def _unfreeze_parent_gc() -> None:
    """Son alt süreç kapanınca dondurmayı geri al (nesneler en yaşlı kuşağa döner; olağan toplama sürer)."""
    global _FREEZE_DEPTH
    with _FREEZE_LOCK:
        if _FREEZE_DEPTH <= 0:
            return
        _FREEZE_DEPTH -= 1
        if _FREEZE_DEPTH == 0:
            gc.unfreeze()


def parent_gc_frozen() -> bool:
    """Testler/teşhis için: ebeveyn GC'si şu an dondurulmuş mu."""
    with _FREEZE_LOCK:
        return _FREEZE_DEPTH > 0


# ------------------------------------------------------------------ kapanış: canlı alt süreç kaydı + atexit kancası
#: Canlı (başlatılmış, kapatılmamış) alt süreçler. Fork + kayıt ve çıkış kancasının kapatması aynı kilit altında:
#: kanca koştuktan sonra yeni alt süreç doğmaz, kancanın göremediği alt süreç kalmaz.
_LIVE: set = set()
_LIVE_LOCK = threading.Lock()
_EXITING = False
_EXIT_HOOK = False


def exiting() -> bool:
    """Süreç çıkıyor mu (çıkış kancası koştu): yeni fork yapılmaz; öldürülen alt süreç arıza sayılmaz."""
    return _EXITING


def _ensure_exit_hook() -> None:
    """İlk fork'ta BİR kez: çıkış kancasını `multiprocessing.util` içe aktarıldıktan SONRA kaydet. `atexit` kancaları ters
    sırada koşar; bizimki `multiprocessing`'in `_exit_function`'ından (daemon alt sürece SIGTERM + zaman aşımsız `join`)
    ÖNCE koşar ve alt süreci öldürmüş olur. Alt süreç hiç kurulmayan süreçte (anahtar kapalı, Linux dışı) kayıt yok."""
    global _EXIT_HOOK
    import multiprocessing.util  # noqa: F401 — `_exit_function` kaydı bizimkinden ÖNCE olsun
    with _LIVE_LOCK:
        if not _EXIT_HOOK:
            atexit.register(_kill_live_children_at_exit)
            _EXIT_HOOK = True


def _kill_live_children_at_exit() -> None:
    """`atexit`: yeni fork'u kapat, canlı alt süreçleri SIGKILL ile öldür ve en çok `EXIT_REAP_S` bekle (biçerek)."""
    global _EXITING
    with _LIVE_LOCK:
        _EXITING = True
        live = list(_LIVE)
    for c in live:
        c.kill()
    for c in live:
        c._reap(EvidenceChild.EXIT_REAP_S)


# ------------------------------------------------------------------ alt süreç tarafı
def _private_kb(pid: int | str = "self") -> int | None:
    """Bir sürecin (varsayılan: bu süreç) özel (paylaşılmayan) belleği, KB (Linux smaps_rollup; yoksa None)."""
    try:
        tot = 0
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as fh:
            for line in fh:
                if line.startswith(("Private_Clean:", "Private_Dirty:")):
                    tot += int(line.split()[1])
        return tot
    except (OSError, ValueError, IndexError):
        return None


_PRCTL: Any = None


def _resolve_prctl() -> None:
    """EBEVEYNDE, fork'tan ÖNCE: `prctl` işlevini çöz (alt süreç dlopen/dlsym yapmasın — fork anında başka bir iş
    parçacığının tuttuğu yükleyici kilidine takılmasın). Linux dışında ya da başarısızsa sessizce None kalır."""
    global _PRCTL
    if _PRCTL is not None:
        return
    try:
        import ctypes
        _PRCTL = ctypes.CDLL(None, use_errno=True).prctl
    except Exception:  # noqa: BLE001 — desteklenmiyorsa boru kapanışı (EOF) yine çıkarır
        _PRCTL = None


def _die_with_parent(parent_pid: int) -> None:
    """Linux: ebeveyn (fork eden iş parçacığı — ömür boyu yaşayan ön ısıtma işçisi) ölünce SIGKILL al. En iyi çaba;
    yalnız ebeveynde önceden çözülmüş işlevi çağırır."""
    fn = _PRCTL
    if fn is not None:
        try:
            fn(1, int(signal.SIGKILL), 0, 0, 0)            # PR_SET_PDEATHSIG = 1
        except Exception:  # noqa: BLE001
            pass
    if os.getppid() != parent_pid:                          # prctl'den önce ebeveyn öldüyse
        os._exit(0)


def _drop_inherited_fds(keep: set[int], sink_fd: int) -> int:
    """ALT SÜREÇTE: 0–2 ve `keep` dışındaki her açık tanımlayıcıyı `sink_fd`'nin (/dev/null) bir kopyasıyla değiştir.

    `dup2` numarayı DOLU tutar: devralınan bir Python dosya/soket nesnesi sonradan kapanırsa yalnız /dev/null kopyasını
    kapatır (yeni açılan bir dosyayı değil). Ebeveynin soketleri (kapatınca FIN gider), boruları (`subprocess`'in
    `communicate`'i EOF görür) ve kilit dosyası bu süreçte tutulmaz. Dönen: değiştirilen sayısı. /proc yoksa 0."""
    try:
        fds = sorted(int(n) for n in os.listdir("/proc/self/fd"))
    except (OSError, ValueError):
        return 0
    n = 0
    for fd in fds:
        if fd <= 2 or fd in keep:
            continue
        try:
            os.fstat(fd)                                    # listelemenin kendi (artık kapalı) tanımlayıcısı atlanır
            os.dup2(sink_fd, fd, inheritable=False)
            n += 1
        except OSError:
            pass
    return n


def _child_main(engine: Any, compute: Callable[[Any, str], dict], conn: Any, nice: int, oom_score_adj: int,
                parent_pid: int = 0, parent_end: Any = None) -> None:
    """Alt süreç döngüsü: ("q", sembol) → ("ok", sembol, kanıt, özel_kb) | ("err", sembol, metin, özel_kb).

    YALNIZ saf hesap: loglama, uyarı, akış yazımı YOK (bkz. modül başlığı). Boru kapanınca ya da ("stop",) gelince çıkar.
    `parent_end`: fork'ta devralınan EBEVEYN boru ucu. İlk iş kapatılır: yoksa ebeveyn ölse bile borunun bir ucu bu
    süreçte açık kalır, `recv` hiç EOF görmez ve alt süreç (PDEATHSIG yoksa) ortada kalır. Ardından devralınan diğer
    tanımlayıcılar /dev/null'a çevrilir (`_drop_inherited_fds`; modül başlığı "DEVRALINAN TANIMLAYICILAR").
    """
    import warnings
    if parent_end is not None:
        with contextlib.suppress(OSError):
            parent_end.close()
    if parent_pid:
        _die_with_parent(parent_pid)
    gc.freeze()                                    # ebeveynden gelen nesneler bu sürecin GC'sinde dolaşılmaz
    try:
        sink = open(os.devnull, "w", encoding="utf-8")     # noqa: SIM115 — süreç ömrü boyunca açık kalır
        sys.stdout = sys.stderr = sink
        _drop_inherited_fds({conn.fileno(), sink.fileno()}, sink.fileno())
    except (OSError, ValueError):
        pass
    warnings.simplefilter("ignore")
    # SIGINT/SIGTERM YOK SAYILIR: `KillMode=control-group` birimde systemd SIGTERM'i bütün sürece yollar; worker son
    # turunu bitirirken alt süreç ölmesin (kapatmayı ebeveyn yapar; SIGKILL, PDEATHSIG ve boru EOF'u yine geçerli).
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except (OSError, ValueError):
            pass
    if nice:
        try:
            os.nice(int(nice))
        except OSError:
            pass
    if oom_score_adj:
        try:
            with open("/proc/self/oom_score_adj", "w", encoding="ascii") as fh:
                fh.write(str(int(oom_score_adj)))
        except OSError:
            pass
    try:
        conn.send(("ready", os.getpid()))
        while True:
            msg = conn.recv()
            if not (isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "q"):
                return                             # ("stop",) ya da bilinmeyen ileti → çık
            sym = msg[1]
            try:
                reply = ("ok", sym, compute(engine, sym), _private_kb())
            except Exception as exc:  # noqa: BLE001 — sembol hatası süreci öldürmez; metni ebeveyne taşınır
                reply = ("err", sym, f"{type(exc).__name__}: {exc}"[:300], _private_kb())
            try:
                conn.send(reply)
            except (OSError, EOFError):
                return
            except Exception as exc:  # noqa: BLE001 — sonuç pickle edilemedi
                conn.send(("err", sym, f"pickle: {type(exc).__name__}: {exc}"[:300], _private_kb()))
    except (EOFError, OSError, KeyboardInterrupt):
        return


# ------------------------------------------------------------------ ebeveyn tarafı
def _pin_callback(wself: "weakref.ref[EvidenceChild]") -> Callable[[Any], None]:
    """Fork anında canlı olan ESKİ bir motorun ölüm kancası (weakref geri çağrısı). Kilitsiz ve istisnasız: motoru
    bırakan iş parçacığında (çoğunlukla tur), motorun içeriği serbest kalmadan ÖNCE koşar. Alt süreç o motorun
    sayfalarını fork'tan devraldı; ebeveyn onları bırakınca sayfalar alt sürecin ÖZEL belleği olurdu (ölçüm: indeks
    boyutu kadar). Alt süreç hemen öldürülür; yeniden fork'u `EvidenceCache` bir sonraki istekte yapar."""
    owner = os.getpid()

    def _cb(_ref: Any) -> None:
        c = wself()
        if c is not None and c._armed and owner == os.getpid():
            c.retire(RETIRE_RETAINED)
    return _cb


class EvidenceChild:
    """Bir yayımlanmış motor için TEK alt süreç. İş parçacığı güvenli DEĞİLDİR: çağıran (`EvidenceCache`) bütün
    kullanımı kendi hesaplama kilidi altında sıralar; yalnız `kill()` kilitsiz çağrılabilir."""

    #: Fork çağrısının kendisi (fork öncesi kancalar dahil) bu kadar sürede dönmezse SIGALRM worker'ı SONLANDIRIR
    #: (deadman; bkz. modül başlığı). Olağan fork 8–16 ms; pay, bellek baskısında yavaş fork'u öldürmemek için geniş.
    FORK_DEADMAN_S = 30.0
    #: Alt süreç fork'tan sonra "hazır" demezse bu kadar beklenir (ebeveyn bu sırada GIL tutmaz).
    START_TIMEOUT_S = 30.0
    #: İstek sürerken alt süreç bu kadar süre HİÇ CPU harcamazsa asılı sayılır ve öldürülür (Linux /proc; yoksa
    #: yalnız üst sınır geçerli). Yavaş ama çalışan (CPU kotası/nice ile kısılmış) alt süreç CPU harcadığı için
    #: öldürülmez.
    STALL_TIMEOUT_S = 60.0
    #: Bir sembolün (LONG+SHORT) kanıtı için mutlak üst sınır (yedek). VPS'te beklenen ~25 sn (138.891 olayda sorgu
    #: başına 12,6 sn); CPU kotası altında kısılmış alt süreç için geniş pay. Aşılırsa alt süreç öldürülür ve çağıran
    #: süreç içi hesaplar (bugünkü yol).
    REQUEST_TIMEOUT_S = 1800.0
    #: Alt sürecin göreli önceliği (yalnız hız). Worker `Nice=5` ile koşar → alt süreç 15.
    NICE = 10
    #: OOM'da çekirdek önce alt süreci seçsin (ebeveyn değil). Yükseltmek yetki istemez. Birimin kendisi ne yapar:
    #: systemd varsayılanı `OOMPolicy=stop` bütün birimi durdurur (modül başlığı, "Bellek").
    OOM_SCORE_ADJ = 1000
    #: Süreç çıkarken (atexit) SIGKILL'lenen alt sürecin biçilmesi için en çok bekleme (sn). SIGKILL'lenen süreç
    #: milisaniyeler içinde biter; pay yalnız aşırı yüklü makine içindir.
    EXIT_REAP_S = 2.0
    #: BELLEK KORUMASI (düzeltme turu 3; modül başlığı "BELLEK"). Alt süreç yaşarken en dar bellek payı
    #: (`memory_headroom_mb`: cgroup sınırı − dosya önbelleği hariç kullanım, ya da sistem MemAvailable) bu değerin
    #: altına inerse alt süreç öldürülür ve iş süreç içi sürer (bugünkü yol; kanıt aynı). Pay en çok
    #: `MEMORY_CHECK_S`'de bir okunur; alt sürecin kendi büyümesi (yazınca-kopyala) dakikalara yayılır.
    MEMORY_KILL_HEADROOM_MB = 512.0
    #: Fork ancak pay bu değerin üstündeyse yapılır (alt sürecin büyüyeceği yer + öldürme eşiği); değilse fork YOK.
    MEMORY_FORK_HEADROOM_MB = 1536.0
    #: Bellek payının okunma aralığı (sn), istek ve el sıkışması beklenirken.
    MEMORY_CHECK_S = 1.0

    def __init__(self, engine: Any, version: int) -> None:
        self.version = int(version)
        self._engine_ref = weakref.ref(engine)     # ebeveyn eski motoru bu nesne yüzünden TUTMAZ
        self._proc: Any = None
        self._conn: Any = None
        self._frozen = False
        self.pid: int | None = None
        self.computed = 0
        self.private_kb_max: int | None = None
        self.started_at = time.monotonic()
        #: Bilinçli öldürme nedeni (`RETIRE_*`); boşsa alt süreç arızasızdır. Kilitsiz yazılır (`retire`).
        self.retired = ""
        self.retired_detail = ""
        #: Fork anında canlı olan eski motorların ölüm kancaları (`_pin_callback`); yalnız `_armed` iken etkili.
        self._pins: list = []
        self._armed = False
        #: Fork'tan hemen önce ölçülen bellek payı (MB; yalnız teşhis) ve son pay okumasının anı (monotonic).
        self.headroom_mb_at_fork: float | None = None
        self._mem_at = time.monotonic()

    # ---------------------------------------------------------------- yaşam döngüsü
    @classmethod
    def start(cls, engine: Any, compute: Callable[[Any, str], dict], *, version: int,
              marker: Path | None = None, watch: Any = ()) -> "EvidenceChild":
        """`fork` ile alt süreci başlat ve "hazır" el sıkışmasını bekle. Başarısızlık → `EvidenceChildError`.

        Fork deadman altında yapılır (`FORK_DEADMAN_S`); `marker` verilirse fork süresince işaret dosyası durur (asılıp
        sonlandırılan worker'ı yeniden başlayan worker tanır: `fork_hung_before`). Süreç çıkıyorsa (`exiting()`) fork
        yapılmaz; başlayan alt süreç çıkış kancasının kaydına fork'la AYNI kilit altında girer.

        BELLEK: bellek payı `MEMORY_FORK_HEADROOM_MB`'in altındaysa fork YAPILMAZ (`EvidenceChildMemoryError`).
        `watch`: ebeveynin bildiği ESKİ motorların ZAYIF referansları (güçlü referans tutulmaz). Fork anında canlı
        olanlara ölüm kancası kurulur: biri ebeveynde serbest kalınca alt süreç `RETIRE_RETAINED` ile hemen öldürülür
        (o motorun sayfaları alt süreçte kalıp özel belleğe dönüşmesin)."""
        if not supported():
            raise EvidenceChildError(f"platform desteklenmiyor: {sys.platform}")
        import multiprocessing as mp
        try:
            ctx = mp.get_context("fork")
        except ValueError as exc:                  # platform fork vermiyor
            raise EvidenceChildError(f"fork yok: {exc}") from exc
        head = _headroom_or_none()
        if head is not None and head[0] < cls.MEMORY_FORK_HEADROOM_MB:
            raise EvidenceChildMemoryError(f"bellek payı yetersiz ({head[1]}: {head[0]:.0f} MB < "
                                           f"{cls.MEMORY_FORK_HEADROOM_MB:.0f} MB); fork yapılmadı")
        _ensure_exit_hook()
        try:
            self = cls(engine, version)
        except TypeError as exc:                   # zayıf referans desteklemeyen motor: güçlü referans TUTULMAZ
            raise EvidenceChildError(f"motor zayıf referans desteklemiyor: {exc}") from exc
        self.headroom_mb_at_fork = None if head is None else round(head[0], 1)
        self._pin(engine, watch)
        _resolve_prctl()
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        _freeze_parent_gc()
        self._frozen = True
        try:
            proc = ctx.Process(target=_child_main, name="pattern-evidence-child", daemon=True,
                               args=(engine, compute, child_conn, cls.NICE, cls.OOM_SCORE_ADJ, os.getpid(),
                                     parent_conn))
            with _LIVE_LOCK:
                if _EXITING:
                    raise EvidenceChildError("süreç kapanıyor; fork yapılmadı")
                with _fork_deadman(cls.FORK_DEADMAN_S):
                    marked = _mark(marker)
                    self._armed = True             # bundan sonra ölen eski motor alt süreçte kalmış olabilir
                    try:
                        proc.start()               # start() hedef/argüman referanslarını bırakır (motor tutulmaz)
                    finally:
                        if marked:                 # deadman süreci sonlandırdıysa buraya gelinmez: dosya kalır
                            _unmark(marker)
                self._proc = proc                  # çıkış kancası artık görür (kill → bekleyen el sıkışması EOF)
                _LIVE.add(self)
        except BaseException as exc:
            self._disarm()
            child_conn.close()
            parent_conn.close()
            self._release_freeze()
            if isinstance(exc, EvidenceChildError):
                raise
            if isinstance(exc, Exception):
                raise EvidenceChildError(f"alt süreç başlatılamadı: {type(exc).__name__}: {exc}") from exc
            raise
        child_conn.close()
        self._conn = parent_conn
        try:
            msg = self._recv(cls.START_TIMEOUT_S, what="hazır el sıkışması")
            if not (isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "ready"):
                raise EvidenceChildError(f"beklenmeyen el sıkışması: {msg!r}"[:200])
            self.pid = int(msg[1])
        except EvidenceChildError as exc:
            self.close()
            if self.retired == RETIRE_MEMORY:      # el sıkışması sırasında bellek koruması: fork reddiyle aynı
                raise EvidenceChildMemoryError(str(exc)) from exc
            if self.retired == RETIRE_RETAINED:    # eski motor el sıkışması sırasında öldü: yeniden fork edilebilir
                raise EvidenceChildRetainedError(str(exc)) from exc
            raise
        except BaseException:
            self.close()
            raise
        return self

    @property
    def alive(self) -> bool:
        p = self._proc
        try:
            return p is not None and self._conn is not None and p.is_alive()
        except (ValueError, AssertionError):
            return False

    def serves(self, engine: Any) -> bool:
        """Bu alt süreç `engine` için mi kuruldu, canlı mı ve bilinçli öldürülmedi mi (kimlik karşılaştırması; sürüm
        karışamaz)."""
        return not self.retired and self.alive and self._engine_ref() is engine

    def engine_is(self, engine: Any) -> bool:
        """Bu alt süreç `engine` için mi kuruldu (canlılıktan bağımsız; yalnız kimlik)."""
        return self._engine_ref() is engine

    def _pin(self, engine: Any, watch: Any) -> None:
        """Fork'tan ÖNCE: `watch`'taki canlı eski motorlara ölüm kancası kur (kendi motoru hariç). Güçlü referans
        TUTULMAZ; kancalar `_armed` olana dek (fork'tan hemen önce) etkisizdir — fork'tan önce ölen motor alt sürece
        hiç geçmez."""
        cb = _pin_callback(weakref.ref(self))
        for r in watch or ():
            e = r() if callable(r) else None
            if e is not None and e is not engine:
                try:
                    self._pins.append(weakref.ref(e, cb))
                except TypeError:
                    pass
            e = None
        del cb

    def _disarm(self) -> None:
        self._armed = False
        self._pins = []

    def retire(self, reason: str, detail: str = "") -> None:
        """Kilitsiz, her iş parçacığından: alt süreci BİLİNÇLİ olarak hemen öldür (`RETIRE_*`; arıza değildir). İlk
        neden kalır. Bekleyen `compute` EOF görür; çağıran `retired`'a bakıp arıza saymaz."""
        if not self.retired:
            self.retired_detail = str(detail)[:300]
            self.retired = str(reason)
        self.kill()

    def kill(self) -> None:
        """Kilitsiz, her iş parçacığından: alt süreci hemen öldür (bekleyen `compute` EOF görür)."""
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except (OSError, ValueError, AttributeError):
                pass

    def close(self, timeout: float = 2.0) -> None:
        """Alt süreci kapat (önce kibarca, sonra SIGKILL), boruyu kapat, ebeveyn GC dondurmasını bırak. Tekrar çağrılabilir."""
        p, c = self._proc, self._conn
        self._conn = None
        self._disarm()
        try:
            if c is not None:
                try:
                    c.send(("stop",))
                except (OSError, EOFError, ValueError):
                    pass
            if p is not None and not self._reap(timeout):
                self.kill()
                self._reap(timeout)
        finally:
            if c is not None:
                try:
                    c.close()
                except OSError:
                    pass
            if p is not None:
                try:
                    p.close()
                except (ValueError, AttributeError):
                    pass
            self._proc = None
            with _LIVE_LOCK:
                _LIVE.discard(self)
            self._release_freeze()

    def _reap(self, timeout: float) -> bool:
        """Alt süreç bitene (ve biçilene) dek en çok `timeout` sn bekle; bitti mi. `waitpid` yoklaması: alt süreç
        `multiprocessing`'in bekçi borusunu da /dev/null'a çevirdiği için (`_drop_inherited_fds`) `join(timeout)`'a
        güvenilmez — bekçi erken EOF verir ve `join` süresiz `waitpid`'e düşer."""
        p = self._proc
        if p is None:
            return True
        end = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                if not p.is_alive():
                    return True
            except (ValueError, AssertionError):          # başka iş parçacığı kapattı / bu süreç ebeveyn değil
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)

    def _release_freeze(self) -> None:
        if self._frozen:
            self._frozen = False
            _unfreeze_parent_gc()

    # ---------------------------------------------------------------- istek
    def _cpu_ticks(self) -> int | None:
        """Alt sürecin harcadığı CPU (utime+stime, saat tıkı; Linux /proc). Okunamazsa None."""
        pid = self.pid
        if not pid:
            return None
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
                rest = fh.read().rsplit(")", 1)[1].split()
            return int(rest[11]) + int(rest[12])
        except (OSError, ValueError, IndexError):
            return None

    def _memory_check(self, what: str) -> None:
        """En çok `MEMORY_CHECK_S`'de bir: bellek payı öldürme eşiğinin altındaysa alt süreci `RETIRE_MEMORY` ile öldür
        → `EvidenceChildError`. Her istekten önce ve istek beklenirken çağrılır."""
        now = time.monotonic()
        if now - self._mem_at < self.MEMORY_CHECK_S:
            return
        self._mem_at = now
        head = _headroom_or_none()
        if head is not None and head[0] < self.MEMORY_KILL_HEADROOM_MB:
            msg = (f"{what}: {head[1]} payı {head[0]:.0f} MB < {self.MEMORY_KILL_HEADROOM_MB:.0f} MB "
                   "(alt süreç öldürüldü)")
            self.retire(RETIRE_MEMORY, msg)
            raise EvidenceChildError(f"bellek koruması — {msg}")

    def _recv(self, timeout: float, *, what: str, stall: float | None = None) -> Any:
        """Zaman aşımlı, ölüm, asılma ve bellek payı algılayan alım. Beklerken GIL tutulmaz (poll = select)."""
        c, p = self._conn, self._proc
        if c is None or p is None:
            raise EvidenceChildError("alt süreç kapalı")
        now = time.monotonic()
        deadline = now + float(timeout)
        ticks, progressed_at = self._cpu_ticks(), now
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                self.kill()
                raise EvidenceChildError(f"{what}: {timeout:.0f} sn içinde cevap yok (alt süreç öldürüldü)")
            try:
                if c.poll(min(self.MEMORY_CHECK_S, 1.0, remaining)):
                    return c.recv()
            except (EOFError, OSError) as exc:
                raise EvidenceChildError(f"{what}: alt süreç bağlantısı koptu ({type(exc).__name__}; "
                                         f"exitcode {getattr(p, 'exitcode', None)})") from exc
            if not p.is_alive():
                raise EvidenceChildError(f"{what}: alt süreç öldü (exitcode {p.exitcode})")
            self._memory_check(what)
            if stall is not None and ticks is not None:
                cur = self._cpu_ticks()
                if cur is not None and cur != ticks:
                    ticks, progressed_at = cur, time.monotonic()
                elif time.monotonic() - progressed_at >= stall:
                    self.kill()
                    raise EvidenceChildError(f"{what}: alt süreç {stall:.0f} sn CPU harcamadı — asılı sayıldı "
                                             "(öldürüldü)")

    def compute(self, symbol: str, timeout: float | None = None) -> dict:
        """Sembolün LONG+SHORT kanıtını alt süreçte hesaplat. Alt süreç arızası → `EvidenceChildError`;
        alt süreçte hesap istisnası → `EvidenceChildComputeError`."""
        c = self._conn
        if c is None or not self.alive:
            raise EvidenceChildError("alt süreç canlı değil")
        self._memory_check(f"{symbol} kanıtı")
        try:
            c.send(("q", str(symbol)))
        except (OSError, EOFError, ValueError) as exc:
            raise EvidenceChildError(f"istek gönderilemedi: {type(exc).__name__}: {exc}") from exc
        msg = self._recv(self.REQUEST_TIMEOUT_S if timeout is None else timeout, what=f"{symbol} kanıtı",
                         stall=self.STALL_TIMEOUT_S)
        if not (isinstance(msg, tuple) and len(msg) == 4 and msg[0] in ("ok", "err") and msg[1] == symbol):
            self.kill()
            raise EvidenceChildError(f"beklenmeyen cevap: {str(msg)[:200]}")
        kb = msg[3]
        if isinstance(kb, int):
            self.private_kb_max = kb if self.private_kb_max is None else max(self.private_kb_max, kb)
        if msg[0] == "err":
            raise EvidenceChildComputeError(str(msg[2]))
        self.computed += 1
        return msg[2]


__all__ = ["MARKER_NAME", "RETIRE_MEMORY", "RETIRE_RETAINED", "RETIRE_SUPERSEDED", "EvidenceChild",
           "EvidenceChildComputeError", "EvidenceChildError", "EvidenceChildMemoryError", "EvidenceChildRetainedError",
           "blas_threads",
           "deadman_unavailable", "exiting", "fork_hung_before", "memory_headroom_mb", "parent_gc_frozen", "supported"]
