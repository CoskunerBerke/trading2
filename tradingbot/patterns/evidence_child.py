"""PATTERN KANITI ALT SÜRECİ (2026-10-05) — kNN kanıt sorguları worker'ın GIL'ini PAYLAŞMAZ.

Sorun (VPS günlüğü, 2026-10-02..05; yerelde sentetik ölçümle yeniden üretildi): 4h kapanışından sonra yenileyici yeni
indeksi yayımlayınca ön ısıtma işçisi (`evidence_cache`) worker SÜRECİNİN İÇİNDE dakikalarca kNN sorgusu koşar. Sorgu
döngüsü olay başına `np.corrcoef` çağırır; bu çağrı BLAS içinde GIL'i çok kısa aralıklarla bırakıp geri alır. CPython'da
GIL bekleyen iş parçacığı ancak 5 ms boyunca hiç el değiştirmezse "bırak" ister; sık bırak-al yapan bir iş parçacığı bu
sayacı sürekli tazelediği için diğer iş parçacıkları (tur, Box zamanlayıcısı, koruyucu izleyici) GIL'i ancak yarışı
şans eseri kazanınca alır. Ölçüm (yerel, 4 çekirdek, aynı kod): arka planda sorgu koşarken 1 ms uyuyan bir iş
parçacığının GIL'i geri alma gecikmesi p50 36 ms / en çok 370 ms (sorgu yokken 0,1 ms; saf Python yükte 5 ms). Turun
numpy/pandas, soket ve dosya ağırlıklı adımları 33–145 kat, saf Python adımları ~10 kat yavaşladı — VPS'teki 43 dk'lık
turlar (semboller 544 sn, yürütme 856 sn) ve Box'ın kaçırdığı 5m mum bununla tutarlıdır. Alt süreçle aynı ölçüm
0,8–1,1 kat. Ayrıntı: docs/TOUR_CONTENTION_V1.md.

Çözüm: sorgular `fork` ile ayrılan TEK bir alt süreçte koşar. Alt süreç yayımlanmış paketin motorunu kopyalamadan
(yazınca-kopyala sayfalar) görür; yani motor, fonksiyon (`TradingEngineV3._evidence_query`) ve girdiler turunkiyle AYNI
nesnelerdir, kanıt bit-bit aynıdır (pickle float'ı kayıpsız taşır; test kilitli). Ebeveyn yalnız boru üzerinden sonucu
bekler — beklerken GIL'i tutmaz. Alt süreç yalnız bir ön ısıtma işi süresince yaşar (iş bitince ya da daha yeni yayım
gelince kapanır) ve o süre içinde turun önbellek ıskaları da ona gider (`EvidenceCache.compute`).

Neden `fork` (spawn/forkserver değil): yeni süreç indeksi ya diskten yeniden kurmalı (40+ sn CPU, yayımlananla aynı
olduğunun kanıtı yok) ya da ebeveynden pickle ile almalıdır (152k olayda yüzlerce MB, ebeveynde GIL'i saniyelerce
tutan tek bir C çağrısı ve iş boyunca tam ikinci kopya). `fork` indeksin aynı bellek görüntüsünü bedelsiz verir.

Çok iş parçacıklı süreçte `fork` riskleri ve önlemler:
* Alt süreç yalnız saf hesap yapar: loglamaz, uyarı basmaz (`warnings` susturulur), stdout/stderr /dev/null'a
  yönlenir — başka bir iş parçacığının `fork` anında tuttuğu akış/log kilidine hiç dokunmaz. İçe aktarma kilidini
  CPython `fork` sırasında yeniden kurar.
* Asılma ya da ölüm ebeveyni bekletmez: hazır el sıkışması (`START_TIMEOUT_S`); istek sürerken alt süreç
  `STALL_TIMEOUT_S` boyunca HİÇ CPU harcamazsa (kilitlenme: hesap yapan süreç CPU harcar, kilitte bekleyen harcamaz)
  öldürülür; ayrıca istek başına üst sınır (`REQUEST_TIMEOUT_S`); boru kapanınca (ölüm/OOM) anında
  `EvidenceChildError`. Çağıran bugünkü süreç içi yola düşer.
* Alt süreç ebeveynden uzun yaşamaz: Linux'ta `PR_SET_PDEATHSIG=SIGKILL` (ebeveyn ölünce çekirdek öldürür; aksi hâlde
  devraldığı tekil kilit dosyası tanımlayıcısı yeniden başlatılan worker'ı bekletebilirdi), ayrıca boru kapanınca çıkar.
* Bellek: alt sürecin GC'si `gc.freeze()` ile ebeveynden gelen nesneleri DOLAŞMAZ; ebeveyn de çocuk yaşarken kendi
  GC'sini dondurur (`_freeze_parent_gc`) — yoksa ebeveynin tam toplaması bütün kapsayıcı başlıklarına yazar ve ortak
  sayfaları kopyalatır (ölçüldü: 335 MB'lık süreçte +68 MB; dondurunca 0). Kalan kopya, sorgunun dokunduğu nesnelerin
  başvuru sayaçlarından gelir: ölçülen alt süreç özel belleği ≈ indeks boyutunun %49'u (50,7k olay ≈ 233 MB indeks →
  113 MB). Alt süreç OOM'da ÖNCE ölsün diye `oom_score_adj=1000`, CPU'da turu itmesin diye `nice +10` alır.
* Platform `fork` vermiyorsa (Windows) ya da `fork` başarısızsa: hata loglanır, yol bugünkü süreç içi hesaptır.

Karar-nötrlük: yayım anı, hangi turun hangi sürümü gördüğü (tek okuma), önbellek anahtarı ve kanıt DEĞİŞMEZ; bu modül
yalnız sorgunun hangi süreçte koştuğunu değiştirir. Geri dönüş anahtarı: `history.evidence_subprocess: false` →
bugünkü süreç içi yol (alt süreç hiç kurulmaz). Anahtar iki değerinde de karar-nötr olduğu için karar kimliğine
(`TradingEngineV3.config_hash`) girmez.
"""
from __future__ import annotations

import gc
import os
import threading
import time
import weakref
from typing import Any, Callable


class EvidenceChildError(RuntimeError):
    """Alt süreç kullanılamaz: başlamadı, öldü, zaman aşımı ya da protokol hatası. Çağıran süreç içi yola düşer."""


class EvidenceChildComputeError(RuntimeError):
    """Alt süreç sağlam; sembolün kanıt hesabı alt süreçte istisna verdi (metin taşınır)."""


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


# ------------------------------------------------------------------ alt süreç tarafı
def _private_kb() -> int | None:
    """Bu sürecin özel (paylaşılmayan) belleği, KB (Linux smaps_rollup; yoksa None)."""
    try:
        tot = 0
        with open("/proc/self/smaps_rollup", encoding="ascii") as fh:
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
    except Exception:  # noqa: BLE001 — desteklenmiyorsa boru kapanışı yine çıkarır
        _PRCTL = None


def _die_with_parent(parent_pid: int) -> None:
    """Linux: ebeveyn (fork eden iş parçacığı — ömür boyu yaşayan ön ısıtma işçisi) ölünce SIGKILL al. En iyi çaba;
    yalnız ebeveynde önceden çözülmüş işlevi çağırır."""
    import signal
    fn = _PRCTL
    if fn is not None:
        try:
            fn(1, int(signal.SIGKILL), 0, 0, 0)            # PR_SET_PDEATHSIG = 1
        except Exception:  # noqa: BLE001
            pass
    if os.getppid() != parent_pid:                          # prctl'den önce ebeveyn öldüyse
        os._exit(0)


def _child_main(engine: Any, compute: Callable[[Any, str], dict], conn: Any, nice: int, oom_score_adj: int,
                parent_pid: int = 0) -> None:
    """Alt süreç döngüsü: ("q", sembol) → ("ok", sembol, kanıt, özel_kb) | ("err", sembol, metin, özel_kb).

    YALNIZ saf hesap: loglama, uyarı, akış yazımı YOK (bkz. modül başlığı). Boru kapanınca ya da ("stop",) gelince çıkar.
    """
    import signal
    import sys
    import warnings
    if parent_pid:
        _die_with_parent(parent_pid)
    gc.freeze()                                    # ebeveynden gelen nesneler bu sürecin GC'sinde dolaşılmaz
    try:
        sink = open(os.devnull, "w", encoding="utf-8")     # noqa: SIM115 — süreç ömrü boyunca açık kalır
        sys.stdout = sys.stderr = sink
    except OSError:
        pass
    warnings.simplefilter("ignore")
    for sig, act in ((signal.SIGINT, signal.SIG_IGN), (signal.SIGTERM, signal.SIG_DFL)):
        try:
            signal.signal(sig, act)
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
class EvidenceChild:
    """Bir yayımlanmış motor için TEK alt süreç. İş parçacığı güvenli DEĞİLDİR: çağıran (`EvidenceCache`) bütün
    kullanımı kendi hesaplama kilidi altında sıralar; yalnız `kill()` kilitsiz çağrılabilir."""

    #: Alt süreç "hazır" demezse (asılı `fork`) bu kadar beklenir.
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
    #: OOM'da önce alt süreç ölsün (ebeveyn değil). Yükseltmek yetki istemez.
    OOM_SCORE_ADJ = 1000

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

    # ---------------------------------------------------------------- yaşam döngüsü
    @classmethod
    def start(cls, engine: Any, compute: Callable[[Any, str], dict], *, version: int) -> "EvidenceChild":
        """`fork` ile alt süreci başlat ve "hazır" el sıkışmasını bekle. Başarısızlık → `EvidenceChildError`."""
        import multiprocessing as mp
        try:
            ctx = mp.get_context("fork")
        except ValueError as exc:                  # platform fork vermiyor
            raise EvidenceChildError(f"fork yok: {exc}") from exc
        try:
            self = cls(engine, version)
        except TypeError as exc:                   # zayıf referans desteklemeyen motor: güçlü referans TUTULMAZ
            raise EvidenceChildError(f"motor zayıf referans desteklemiyor: {exc}") from exc
        _resolve_prctl()
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        _freeze_parent_gc()
        self._frozen = True
        try:
            proc = ctx.Process(target=_child_main, name="pattern-evidence-child", daemon=True,
                               args=(engine, compute, child_conn, cls.NICE, cls.OOM_SCORE_ADJ, os.getpid()))
            proc.start()                           # start() hedef/argüman referanslarını bırakır (motor tutulmaz)
        except BaseException as exc:
            child_conn.close()
            parent_conn.close()
            self._release_freeze()
            if isinstance(exc, Exception):
                raise EvidenceChildError(f"alt süreç başlatılamadı: {type(exc).__name__}: {exc}") from exc
            raise
        child_conn.close()
        self._proc, self._conn = proc, parent_conn
        try:
            msg = self._recv(cls.START_TIMEOUT_S, what="hazır el sıkışması")
            if not (isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "ready"):
                raise EvidenceChildError(f"beklenmeyen el sıkışması: {msg!r}"[:200])
            self.pid = int(msg[1])
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
        """Bu alt süreç `engine` için mi kuruldu ve canlı mı (kimlik karşılaştırması; sürüm karışamaz)."""
        return self.alive and self._engine_ref() is engine

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
        try:
            if c is not None:
                try:
                    c.send(("stop",))
                except (OSError, EOFError, ValueError):
                    pass
            if p is not None:
                try:
                    p.join(timeout)
                    if p.is_alive():
                        p.kill()
                        p.join(timeout)
                except (OSError, ValueError, AssertionError):
                    pass
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
            self._release_freeze()

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

    def _recv(self, timeout: float, *, what: str, stall: float | None = None) -> Any:
        """Zaman aşımlı, ölüm ve asılma algılayan alım. Beklerken GIL tutulmaz (poll = select)."""
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
                if c.poll(min(1.0, remaining)):
                    return c.recv()
            except (EOFError, OSError) as exc:
                raise EvidenceChildError(f"{what}: alt süreç bağlantısı koptu ({type(exc).__name__}; "
                                         f"exitcode {getattr(p, 'exitcode', None)})") from exc
            if not p.is_alive():
                raise EvidenceChildError(f"{what}: alt süreç öldü (exitcode {p.exitcode})")
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


__all__ = ["EvidenceChild", "EvidenceChildComputeError", "EvidenceChildError", "parent_gc_frozen"]
