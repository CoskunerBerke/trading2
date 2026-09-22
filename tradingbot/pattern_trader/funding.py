# -*- coding: utf-8 -*-
"""FUNDING VERİSİ — formasyon defterinin muhasebesine bağlanan DOĞRULANMIŞ settlement oranları.

Sözleşme (2026-09-17):
* `lookup(symbol, when)` YALNIZ GERÇEKLEŞMİŞ settlement satırından oran döndürür (`/fapi/v1/fundingRate`).
  `premiumIndex.lastFundingRate` o ANIN tahminidir ve geçmiş bir settlement'a ASLA uygulanmaz.
* Oran bilinmiyorsa `None` döner. Defter o dönemi BEKLETİR (`FundingSchedule.accrue` watermark'ı ilerletmez):
  bilinmeyen funding sıfır maliyet olarak net performansa GİRMEZ.
* Settlement saatleri sözleşmeye göredir: `/fapi/v1/fundingInfo` yalnız VARSAYILANDAN (8 saat) sapan sembolleri
  yayımlar; listede olmayan sembol varsayılandadır. Aralık çözülemezse `hours_for` boş demet döner ve o sembol
  için hiçbir dönem üretilmez — sekiz saat varsayımı yeni sözleşmelere doğrulanmadan yayılmaz.
* AĞ İSTEĞİ KORUYUCU ÇIKIŞ YOLUNDA DEĞİLDİR (2026-09-22): `lookup`/`hours_for`/`settlement_mark` yalnız bellekten
  okur (defterin tick'i bunları kilit altında, stop kontrolünden önce çağırır). Ağ yalnız `refresh()`tedir; onu
  tarayıcı iş parçacığı çağırır ve istek sürerken bu nesnenin kilidi TUTULMAZ. Ağ arızası yutulur, `stats` sayar.
* Aynı settlement iki kez yazılmaz: yinelenmezliği defterin `last_funding_settlement_utc` watermark'ı sağlar
  (bu servis saf bir ORAN KAYNAĞIDIR, muhasebe yazmaz).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from ..core import D, FUNDING_HOURS_UTC
from ..market.providers import now_ms as _now_ms

log = logging.getLogger(__name__)

#: Bir settlement damgasının eşleşme toleransı (borsa saniye/ms yuvarlamaları için).
MATCH_TOLERANCE_MS = 60_000
#: Sembol başına geçmiş penceresi: pozisyon ömrü bundan uzunsa gerektikçe geriye doğru genişletilir.
DEFAULT_LOOKBACK_MS = 8 * 86_400_000
#: Sözleşme aralığı tablosunun tazeleme aralığı (yeni listelenen sözleşme sapan aralıkla gelebilir).
INFO_TTL_S = 3600.0
#: Aralık tablosu alınamadığında yeniden deneme aralığı (her tahakkukta uca yüklenmemek için negatif önbellek).
FAILED_INFO_RETRY_S = 60.0
#: Sembol tablosunun tazeleme aralığı (settlement ~8 saatte bir; sık sorgu ağırlık israfıdır).
RATE_TTL_S = 900.0


def hours_from_interval(h: Any) -> tuple[int, ...] | None:
    """`fundingIntervalHours` → UTC settlement saatleri. 24'ü tam bölmeyen aralık çözülemez → None."""
    try:
        n = int(h)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n > 24 or 24 % n != 0:
        return None
    return tuple(range(0, 24, n))


class FundingRates:
    """Sembol → {settlement_ms: oran} önbelleği + sözleşme aralığı tablosu. İş parçacığı güvenli (tarayıcı ve
    çıkış izleyicisi aynı nesneyi kullanır). `provider`: `funding_history(symbol, limit, start_ms, end_ms)` ve
    isteğe bağlı `funding_info()` veren nesne (duck-typed; testler MockProvider verir)."""

    def __init__(self, provider: Any, *, clock_ms: Callable[[], int] = _now_ms, rate_ttl_s: float = RATE_TTL_S,
                 info_ttl_s: float = INFO_TTL_S, lookback_ms: int = DEFAULT_LOOKBACK_MS,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.provider = provider
        self.clock_ms = clock_ms
        self.monotonic = monotonic
        self.rate_ttl_s = float(rate_ttl_s)
        self.info_ttl_s = float(info_ttl_s)
        self.lookback_ms = int(lookback_ms)
        self._lock = threading.RLock()
        self._rates: dict[str, dict[int, Decimal]] = {}
        self._covered: dict[str, tuple[int, int]] = {}      # sembol → (kapsanan_baslangic_ms, kapsanan_bitis_ms)
        self._fetched_at: dict[str, int] = {}
        self._intervals: dict[str, tuple[int, ...] | None] = {}
        #: settlement anı → o satırın KENDİ mark fiyatı (geç uzlaştırma tutarı için; tahmin yerine geçmez)
        self._marks: dict[str, dict[int, Decimal]] = {}
        #: `lookup`un istediği ama bellekte bulunmayan en erken settlement anı (sembol → ms); `refresh` çeker
        self._wanted: dict[str, int] = {}
        #: `refresh` tekilliği: ikinci eşzamanlı çağrı BEKLEMEZ, atlanır
        self._refresh_lock = threading.Lock()
        self._intervals_at: int | None = None
        #: Sapma tablosu GERÇEKTEN alındı mı? False iken varsayılan 8 saat DOĞRULANMAMIŞTIR ve yayılmaz.
        self._intervals_known: bool = False
        self.stats = {"history_calls": 0, "history_errors": 0, "info_calls": 0, "info_errors": 0,
                      "lookups": 0, "hits": 0, "misses": 0}

    # ------------------------------------------------------------------ AĞ YOLU (yalnız `refresh`) — 2026-09-22
    # KORUYUCU ÇIKIŞ SÖZLEŞMESİ (REVIEW-2026-09-22 F2): `lookup`/`hours_for` defterin tick'i İÇİNDE (defter kilidi
    # altında, stop kontrolünden ÖNCE) çağrılır. Bu yüzden ikisi de ARTIK AĞA ÇIKMAZ: yalnız bellekteki doğrulanmış
    # veriyi okur, eksik olanı "istenen" diye not eder ve None/boş döner (dönem BEKLER). Ağ istekleri yalnız
    # `refresh()` içindedir; onu tarayıcı iş parçacığı çağırır (`PatternScanner.scan_cycle`), 60 sn çıkış izleyicisi
    # ÇAĞIRMAZ. `refresh` ağ isteği sürerken bu nesnenin kilidini TUTMAZ: bekleyen istek, başka iş parçacığındaki
    # `lookup`/tick'i bekletemez.
    def _intervals_due(self, now: int) -> bool:
        return self._intervals_at is None or (now - self._intervals_at) >= self.info_ttl_s * 1000

    def _fetch_intervals(self, now: int) -> None:
        if not hasattr(self.provider, "funding_info"):
            with self._lock:                             # uç YOK: sapma tablosu DOĞRULANAMAZ (fail-closed)
                self._intervals_at = now
            return
        with self._lock:
            self.stats["info_calls"] += 1
        try:
            rows = self.provider.funding_info()          # AĞ — kilit DIŞINDA
        except Exception as exc:  # noqa: BLE001 — aralık tablosu alınamazsa ESKİ (doğrulanmış) tablo korunur
            rows = exc
        with self._lock:
            if isinstance(rows, Exception) or not isinstance(rows, list):
                self.stats["info_errors"] += 1
                if isinstance(rows, Exception):
                    log.warning("funding aralık tablosu alınamadı: %s", rows)
                self._intervals_at = now - int(max(0.0, self.info_ttl_s - FAILED_INFO_RETRY_S) * 1000)  # kısa negatif önbellek
                return
            tbl: dict[str, tuple[int, ...] | None] = {}
            for r in rows:
                if not isinstance(r, dict):
                    continue
                raw = str(r.get("symbol") or "")
                if raw:
                    tbl[raw] = hours_from_interval(r.get("fundingIntervalHours"))
            self._intervals = tbl
            self._intervals_known = True                    # tablo GERÇEKTEN alındı: varsayılan artık doğrulanmıştır
            self._intervals_at = now

    def _history_window(self, symbol: str, want_ms: int | None, now: int) -> tuple[int, int] | None:
        """Bu sembol için ağdan geçmiş istenmeli mi? Döner: (start, end) ya da None. Kilit altında çağrılır."""
        cov = self._covered.get(symbol)
        fetched = self._fetched_at.get(symbol)
        fresh = fetched is not None and (now - fetched) < self.rate_ttl_s * 1000
        # İstenen an ancak settlement'tan SONRA yapılmış bir çekimle kapsanmış sayılır: settlement'tan önce yapılan
        # çekim o satırı içeremez (borsa satırı settlement anında yayımlar).
        covered = want_ms is None or (cov is not None and fetched is not None and cov[0] <= want_ms
                                      and want_ms + MATCH_TOLERANCE_MS <= fetched)
        if fresh and covered:
            return None
        base = int(want_ms) if want_ms is not None else now
        return (min(base - MATCH_TOLERANCE_MS, now - self.lookback_ms), now + MATCH_TOLERANCE_MS)

    def refresh(self, symbols: Any = None, *, now_ms: int | None = None, budget_s: float | None = None) -> dict[str, Any]:
        """AĞ adımı: aralık tablosu (TTL dolduysa) + `symbols` ve bekleyen (`lookup`un istediği) semboller için
        gerçekleşmiş settlement geçmişi. Koruyucu çıkış yolunun DIŞINDA çağrılmalıdır. Aynı anda ikinci çağrı
        beklemez, atlanır. Ağ arızası yutulur ve `stats`a yazılır; bellekteki doğrulanmış veri korunur.

        `budget_s` (2026-09-22): tek çağrının ağda geçirebileceği süre. Aşılınca kalan semboller BU turda
        çekilmez (`budget_exhausted`; dönemleri bekler, bir sonraki adım çeker) — tek iş parçacıklı motor turunda
        yavaş ağ, turu ve dolayısıyla turlar arasındaki çıkış izleyicisini sınırsız uzatmasın. En az bir sembol çekilir."""
        if not self._refresh_lock.acquire(blocking=False):
            return {"skipped": "REFRESH_IN_PROGRESS"}
        try:
            t_start = self.monotonic()
            now = int(now_ms if now_ms is not None else self.clock_ms())
            with self._lock:
                due = self._intervals_due(now)
            if due:
                self._fetch_intervals(now)
            with self._lock:
                wanted = dict(self._wanted)
            names = list(dict.fromkeys([str(s) for s in (symbols or [])] + sorted(wanted)))
            fetched = 0
            attempted = 0
            exhausted: list[str] = []
            for i, sym in enumerate(names):
                # BÜTÇE DENEMEYE göre (başarıya göre DEĞİL): tüm istekler zaman aşımıyla düşse de tur sınırsız uzamaz
                # (doğrulayıcı bulgusu, 2026-09-22). En az bir deneme yapılır; aralık tablosu isteği de süreye dahildir.
                if budget_s is not None and (attempted > 0 or due) and (self.monotonic() - t_start) >= float(budget_s):
                    exhausted = names[i:]
                    break
                with self._lock:
                    win = self._history_window(sym, wanted.get(sym), now)
                    if win is not None:
                        self.stats["history_calls"] += 1
                if win is None:
                    continue
                attempted += 1
                try:
                    rows = self.provider.funding_history(sym, limit=1000, start_ms=win[0], end_ms=win[1]) or []   # AĞ — kilit DIŞINDA
                except Exception as exc:  # noqa: BLE001 — ağ arızası tahakkuku BEKLETİR, çökertmez
                    with self._lock:
                        self.stats["history_errors"] += 1
                    log.warning("%s funding geçmişi alınamadı: %s", sym, exc)
                    continue
                fetched += 1
                with self._lock:
                    tbl = self._rates.setdefault(sym, {})
                    mk = self._marks.setdefault(sym, {})
                    for r in rows:
                        if not isinstance(r, dict) or r.get("rate") is None:
                            continue
                        try:
                            ts = int(r.get("funding_ts"))
                            tbl[ts] = D(r.get("rate"))
                        except (TypeError, ValueError, ArithmeticError):
                            continue
                        try:
                            m = D(r.get("mark")) if r.get("mark") is not None else None
                        except (TypeError, ValueError, ArithmeticError):
                            m = None
                        if m is not None and m > 0:
                            mk[ts] = m
                    prev = self._covered.get(sym)
                    self._covered[sym] = (min(win[0], prev[0]) if prev else win[0], max(win[1], prev[1]) if prev else win[1])
                    self._fetched_at[sym] = now
                    w = self._wanted.get(sym)
                    if w is not None and self._covered[sym][0] <= w and w + MATCH_TOLERANCE_MS <= now:
                        self._wanted.pop(sym, None)       # istenen an artık kapsamda (oran yoksa lookup yeniden ister)
            out = {"intervals_refreshed": bool(due), "symbols": names, "fetched": fetched}
            if exhausted:
                out["budget_exhausted"] = exhausted
            return out
        finally:
            self._refresh_lock.release()

    # ------------------------------------------------------------------ BELLEK OKUMALARI (ağ YOK)
    def hours_for(self, symbol: str) -> tuple[int, ...]:
        """Sembolün settlement saatleri — YALNIZ bellekten (ağ YOK; tablo `refresh` ile yüklenir).

        Sapma tablosu HİÇ alınamadıysa BOŞ demet döner: sekiz saat varsayımı DOĞRULANMADAN yayılmaz (karşıt
        doğrulama bulgusu — aksi hâlde eksik settlement'lar hiç "due" olmaz ve kapsama yanlışlıkla TAM görünür).
        Tablo alındıysa: listede olmayan sembol varsayılandadır; listede ama aralığı çözülemiyorsa BOŞ demet."""
        with self._lock:
            if not self._intervals_known:
                return ()
            raw = str(symbol).replace("/", "")
            if raw not in self._intervals:
                return FUNDING_HOURS_UTC
            return self._intervals[raw] or ()

    def _find(self, tbl: dict[int, Decimal], want: int) -> Decimal | None:
        hit = tbl.get(want)
        if hit is None:
            near = [t for t in tbl if abs(t - want) <= MATCH_TOLERANCE_MS]
            hit = tbl[min(near, key=lambda t: abs(t - want))] if near else None
        return hit

    def lookup(self, symbol: str, when: datetime) -> Decimal | None:
        """`RateLookup` sözleşmesi: settlement anının GERÇEKLEŞMİŞ oranı ya da None. Tahmin ÜRETMEZ, AĞA ÇIKMAZ:
        bellekte yoksa an "istenen" diye not edilir (bir sonraki `refresh` onu çeker) ve None döner — dönem BEKLER."""
        want = int(when.timestamp() * 1000)
        with self._lock:
            self.stats["lookups"] += 1
            hit = self._find(self._rates.get(symbol) or {}, want)
            if hit is None:
                prev = self._wanted.get(symbol)
                self._wanted[symbol] = want if prev is None else min(prev, want)
            self.stats["hits" if hit is not None else "misses"] += 1
            return hit

    def __call__(self, symbol: str, when: datetime) -> Decimal | None:
        """Nesnenin kendisi bir `RateLookup`tır; defter tick'ine KAYNAK olarak verilir (oran + `settlement_mark`)."""
        return self.lookup(symbol, when)

    def mark_basis(self, symbol: str, when: datetime) -> str:
        """Bu kaynağın mark dayanağı: settlement satırının KENDİ mark'ı (vekil yok)."""
        return "SETTLEMENT_ROW"

    def settlement_mark(self, symbol: str, when: datetime) -> Decimal | None:
        """Settlement satırının KENDİ mark fiyatı (`/fapi/v1/fundingRate` → `markPrice`) — yalnız bellekten.
        Geç (pozisyon kapandıktan sonra) uzlaştırılan funding tutarı bununla hesaplanır; yoksa None (tahmin YOK)."""
        want = int(when.timestamp() * 1000)
        with self._lock:
            return self._find(self._marks.get(symbol) or {}, want)

    def pending(self) -> dict[str, int]:
        """`lookup`un istediği ama bellekte olmayan en erken settlement anları (sembol → ms)."""
        with self._lock:
            return dict(self._wanted)

    # ------------------------------------------------------------------ kapsama
    def coverage(self, symbol: str, *, since: datetime, until: datetime) -> dict[str, Any]:
        """(since, until] aralığında kaç settlement bekleniyor, kaçının GERÇEK oranı elimizde — rapor için."""
        from ..core import funding_settlements_between
        hours = self.hours_for(symbol)
        if not hours:
            return {"interval_known": False, "due": None, "known": None, "complete": False, "reason": "INTERVAL_UNKNOWN"}
        due = funding_settlements_between(since, until, hours)
        known = sum(1 for t in due if self.lookup(symbol, t) is not None)
        return {"interval_known": True, "hours_utc": list(hours), "due": len(due), "known": known,
                "complete": known == len(due), "reason": "" if known == len(due) else "RATES_MISSING"}


class LazyProvider:
    """Sağlayıcıyı İLK ağ adımında kurar (`factory()`): motor kurulurken ağ nesnesi açılmaz, test enjeksiyonu
    (`_gap_provider_factory`) kuruluştan sonra verilse de geçerli olur. Yalnız funding uçlarını iletir."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._provider: Any = None

    def _get(self) -> Any:
        if self._provider is None:
            self._provider = self._factory()
        return self._provider

    def funding_history(self, symbol: str, limit: int = 1000, start_ms: int | None = None, end_ms: int | None = None):
        return self._get().funding_history(symbol, limit=limit, start_ms=start_ms, end_ms=end_ms)

    def funding_info(self):
        p = self._get()
        if not hasattr(p, "funding_info"):
            raise AttributeError("sağlayıcı fundingInfo vermiyor")
        return p.funding_info()


__all__ = ["MATCH_TOLERANCE_MS", "DEFAULT_LOOKBACK_MS", "FAILED_INFO_RETRY_S", "FundingRates", "LazyProvider",
           "hours_from_interval"]
