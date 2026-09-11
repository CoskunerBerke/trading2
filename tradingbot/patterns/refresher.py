"""BENZERLIK INDEKSININ CALISMA ZAMANINDA YENILENMESI — restart YOK, kararı BEKLETMEDEN.

Onceki durum: `_load_pattern_engine` indeksi surec basina BIR KEZ kurardı ve bir daha
yenilemezdi. Arsiv guncellense bile calisan worker eski indeksi kullanmaya devam ederdi;
bayatlik kapisi (`now - last_ts > 3 bar`) devreye girince pattern kaniti susar ve AYNI
SURECTE bir daha geri gelmezdi.

Bu modul o bosluğu kapatır. Dort kisit tasarimi belirledi:

1. **Kararı bekletme.** `watch` dongusu TEK IS PARCACIKLIDIR: `exit_check` yalnizca turlar
   ARASINDAKI beklemede calisir. Tur icine konan 40 saniyelik bir yeniden kurulum, stop ve
   TP yonetimini 40 saniye geciktirirdi. Bu yuzden yenileme AYRI BIR IS PARCACIGINDA doner;
   tur ona hicbir zaman blok olmaz.

2. **Yarim veri gorunmesin.** Yeni indeks TAMAMEN kurulduktan sonra TEK bir atama ile
   yayimlanir (`self._bundle = new`). Okuyucu ya eski butunu ya yeni butunu gorur; arada
   bir durum YOKTUR. Okuyucular paketi tek seferde yerel bir degiskene alir.

3. **Onbellek dogru gecersiz kilinsin.** Yayimlanan paketin artan bir `version`u vardir.
   Pattern kaniti onbelleginin anahtari bu surumu icerir, bu yuzden yayim ESKI cevaplari
   erisilemez kilar — ayrica temizlik icin eski surum girdileri budanir.

4. **Bellek sinirlansin.** Onceki OOM'un sebebi tam Tier-A indeksiydi (~578k olay, 3,1 GB).
   Yeniden kurulum sirasinda kisa bir an ESKI ve YENI indeks birlikte yasar; sinirsiz bir
   kume ile bu iki katina cikar. Bu yuzden indeks kumesi `max_symbols` ile baglanir ve
   varsayilan olarak giris evreni ∪ acik pozisyonlardir.

Bayatlik ve toparlanma: arsiv guncellenemezse (ag/veri hatasi) eski indeks KORUNUR ve
durum `last_error` + `last_success_at` ile gorunur kalir. Kaynak duzelince bir sonraki
periyot kaldigi yerden devam eder; elle mudahale gerekmez. Hicbir zaman "guncel gorunsun"
diye damga ilerletilmez.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

#: Indekse alinacak azami sembol. Bellek tavani buradan gelir (bkz. modul basligi 4).
DEFAULT_MAX_SYMBOLS = 16


@dataclass(frozen=True)
class PatternBundle:
    """Yayimlanmis indeks. DEGISMEZ: yenileme yeni bir paket uretir, eskisini duzenlemez."""
    engine: Any
    version: int
    built_at: float
    events: int
    series: int
    last_ts: dict[str, int] = field(default_factory=dict)   # "SYM|market|tf" -> son bar

    def newest_ts(self) -> int | None:
        return max(self.last_ts.values()) if self.last_ts else None


def _series_key(sym: str, market: str, tf: str) -> str:
    return f"{sym}|{market}|{tf}"


class IndexRefresher:
    """Arsivi artimli ilerletir, indeksi yeniden kurar ve ATOMIK yayimlar.

    `build_fn()` cagrildiginda YENI bir `SimilarPatternEngine` (ya da benzeri) dondurmelidir;
    bu modul onu yorumlamaz, yalnizca yayimlar.
    """

    def __init__(self, *, build_fn: Callable[[list[str]], tuple[Any, dict[str, int]]],
                 symbols_fn: Callable[[], list[str]],
                 update_fn: Callable[[list[str], int], Any] | None = None,
                 interval_s: float = 900.0, max_symbols: int = DEFAULT_MAX_SYMBOLS,
                 index_timeframes: Iterable[str] = ("4h",),
                 clock: Callable[[], float] = time.time) -> None:
        self._build_fn = build_fn
        self._symbols_fn = symbols_fn
        self._update_fn = update_fn
        self.interval_s = float(interval_s)
        self.max_symbols = int(max_symbols)
        #: INDEKSIN GERCEKTEN OKUDUGU zaman dilimleri. Yeniden kurulum YALNIZ bunlardan biri
        #: ilerlediginde yapilir. Olculdu: indeks yalnizca 4h serisinden kurulur, ama arsiv
        #: 1h/1d de tasir; her saat kapanan bir 1h bari yuzunden yeniden kurmak 40 sn CPU ve
        #: ~250 sn'lik soguk onbellek turu ureteceği hâlde indekste TEK BIR olay degistirmez.
        self.index_timeframes = tuple(str(t) for t in index_timeframes)
        self._clock = clock
        self._bundle: PatternBundle | None = None
        self._version = 0
        self._lock = threading.Lock()          # YALNIZ kurulum serilestirmesi icin; okuma kilitsiz
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = False
        self.last_attempt_at: float | None = None
        self.last_success_at: float | None = None
        self.last_error: str = ""
        self.refreshes = 0
        self.last_update: dict | None = None

    # ------------------------------------------------------------------ okuma (kilitsiz)
    @property
    def bundle(self) -> PatternBundle | None:
        """Yayimlanmis paket. Tek atribut okumasi — yarim durum GORULEMEZ."""
        return self._bundle

    def symbols(self) -> list[str]:
        """Indekse alinacak, TAVANA baglanmis sembol listesi."""
        out: list[str] = []
        for s in self._symbols_fn() or ():
            s = str(s)
            if s and s not in out:
                out.append(s)
        return out[: self.max_symbols]

    def status(self) -> dict:
        b = self._bundle
        now = self._clock()
        return {
            "enabled": True,
            "interval_s": self.interval_s,
            "running": self._running,
            "refreshes": self.refreshes,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "seconds_since_success": (now - self.last_success_at) if self.last_success_at else None,
            "last_error": self.last_error,
            "max_symbols": self.max_symbols,
            "index_timeframes": list(self.index_timeframes),
            "symbols": self.symbols(),
            "index": None if b is None else {
                "version": b.version, "events": b.events, "series": b.series,
                "built_at": b.built_at, "age_s": round(now - b.built_at, 1),
                "newest_bar_ms": b.newest_ts(),
            },
            "archive_update": self.last_update,
        }

    def _index_inputs_advanced(self, update: dict) -> bool:
        """Yeniden kurulumu YALNIZ indeksin okudugu seriler tetikler.

        `rows_new > 0` yetmez: 1h serisine satir eklenmesi 4h indeksini degistirmez.
        Alan yoksa (eski/yabanci rapor bicimi) fail-safe olarak genel bayrağa duselir.
        """
        rows = update.get("results")
        if not isinstance(rows, list):
            return bool(update.get("advanced"))
        want = set(self.index_timeframes)
        return any(r.get("status") == "advanced" and int(r.get("rows_new") or 0) > 0
                   and str(r.get("timeframe")) in want for r in rows if isinstance(r, dict))

    # ------------------------------------------------------------------ yenileme
    def refresh_once(self, *, now_ms: int | None = None) -> dict:
        """Bir yenileme dongusu: arsivi ilerlet, gerekiyorsa indeksi kur ve yayimla.

        Yeniden kurulum YALNIZ arsiv gercekten ilerlediyse ya da henuz indeks yoksa yapilir.
        Bosuna kurulum yapmak, 40 saniyelik CPU ve iki kat bellek demektir.
        """
        if not self._lock.acquire(blocking=False):
            return {"skipped": "already_running"}
        try:
            self._running = True
            self.last_attempt_at = self._clock()
            syms = self.symbols()
            advanced = self._bundle is None
            if self._update_fn is not None and syms:
                try:
                    rep = self._update_fn(syms, int(now_ms if now_ms is not None else self._clock() * 1000))
                    self.last_update = rep.to_dict() if hasattr(rep, "to_dict") else dict(rep or {})
                    advanced = advanced or self._index_inputs_advanced(self.last_update)
                    errs = self.last_update.get("errors") or {}
                    # ARSIV HATASI YENILEMEYI DURDURMAZ: elde ne varsa onunla devam edilir,
                    # ama hata gorunur kalir ve `last_success_at` ancak yayim olursa ilerler.
                    self.last_error = ("; ".join(f"{k}: {v}" for k, v in list(errs.items())[:3]))[:300]
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"update: {type(exc).__name__}: {exc}"[:300]
                    log.warning("arşiv güncellemesi başarısız (eski indeks korunuyor): %s", exc)
            if not advanced:
                return {"skipped": "archive_unchanged", "index_version": getattr(self._bundle, "version", None)}
            try:
                engine, last_ts = self._build_fn(syms)
            except Exception as exc:  # noqa: BLE001 — kurulum patlarsa ESKI paket korunur
                self.last_error = f"build: {type(exc).__name__}: {exc}"[:300]
                log.warning("indeks kurulamadı (eski indeks korunuyor): %s", exc)
                return {"error": self.last_error}
            if engine is None:
                self.last_error = "build: indeks boş"
                return {"error": self.last_error}
            self._version += 1
            new = PatternBundle(engine=engine, version=self._version, built_at=self._clock(),
                                events=len(getattr(engine, "events", []) or []),
                                series=len(getattr(engine, "candles", {}) or {}),
                                last_ts=dict(last_ts or {}))
            self._bundle = new                      # ATOMIK YAYIM — tek atama
            self.refreshes += 1
            self.last_success_at = new.built_at
            if not (self.last_update or {}).get("errors"):
                self.last_error = ""
            log.info("pattern indeksi yenilendi: sürüm %d, %d olay, %d seri",
                     new.version, new.events, new.series)
            return {"published": True, "index_version": new.version, "events": new.events}
        finally:
            self._running = False
            self._lock.release()

    # ------------------------------------------------------------------ arka plan
    def start(self) -> None:
        """Arka plan is parcacigini baslat. Tur ASLA bu is parcacigina blok OLMAZ."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pattern-refresher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception as exc:  # noqa: BLE001 — is parcacigi ASLA olmez
                self.last_error = f"loop: {type(exc).__name__}: {exc}"[:300]
                log.exception("yenileme döngüsü hatası: %s", exc)
            # Bolunmus bekleme: durdurma istegine hizli cevap verir.
            waited = 0.0
            while waited < self.interval_s and not self._stop.is_set():
                self._stop.wait(min(2.0, self.interval_s - waited))
                waited += 2.0


__all__ = ["DEFAULT_MAX_SYMBOLS", "IndexRefresher", "PatternBundle"]
