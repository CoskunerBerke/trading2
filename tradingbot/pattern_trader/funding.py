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
* Ağ arızası koruyucu çıkışı ENGELLEMEZ: her sağlayıcı çağrısı yutulur, `None`a düşülür ve `stats` sayar.
* Aynı settlement iki kez yazılmaz: yinelenmezliği defterin `last_funding_settlement_utc` watermark'ı sağlar
  (bu servis saf bir ORAN KAYNAĞIDIR, muhasebe yazmaz).
"""
from __future__ import annotations

import logging
import threading
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
                 info_ttl_s: float = INFO_TTL_S, lookback_ms: int = DEFAULT_LOOKBACK_MS) -> None:
        self.provider = provider
        self.clock_ms = clock_ms
        self.rate_ttl_s = float(rate_ttl_s)
        self.info_ttl_s = float(info_ttl_s)
        self.lookback_ms = int(lookback_ms)
        self._lock = threading.RLock()
        self._rates: dict[str, dict[int, Decimal]] = {}
        self._covered: dict[str, tuple[int, int]] = {}      # sembol → (kapsanan_baslangic_ms, kapsanan_bitis_ms)
        self._fetched_at: dict[str, int] = {}
        self._intervals: dict[str, tuple[int, ...] | None] = {}
        self._intervals_at: int | None = None
        #: Sapma tablosu GERÇEKTEN alındı mı? False iken varsayılan 8 saat DOĞRULANMAMIŞTIR ve yayılmaz.
        self._intervals_known: bool = False
        self.stats = {"history_calls": 0, "history_errors": 0, "info_calls": 0, "info_errors": 0,
                      "lookups": 0, "hits": 0, "misses": 0}

    # ------------------------------------------------------------------ sözleşme aralığı
    def _refresh_intervals(self) -> None:
        now = int(self.clock_ms())
        if self._intervals_at is not None and (now - self._intervals_at) < self.info_ttl_s * 1000:
            return
        if not hasattr(self.provider, "funding_info"):
            # Uç YOK: sapma tablosu DOĞRULANAMAZ. Varsayılanı yaymak yerine bilinmiyor sayılır (fail-closed).
            self._intervals_at = now
            return
        self.stats["info_calls"] += 1
        try:
            rows = self.provider.funding_info()
        except Exception as exc:  # noqa: BLE001 — aralık tablosu alınamazsa ESKİ (doğrulanmış) tablo korunur
            self.stats["info_errors"] += 1
            log.warning("funding aralık tablosu alınamadı: %s", exc)
            self._intervals_at = now - int(max(0.0, self.info_ttl_s - FAILED_INFO_RETRY_S) * 1000)  # kısa negatif önbellek
            return
        if not isinstance(rows, list):
            self.stats["info_errors"] += 1
            self._intervals_at = now - int(max(0.0, self.info_ttl_s - FAILED_INFO_RETRY_S) * 1000)
            return
        tbl: dict[str, tuple[int, ...] | None] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            raw = str(r.get("symbol") or "")
            if raw:
                tbl[raw] = hours_from_interval(r.get("fundingIntervalHours"))
        self._intervals = tbl
        self._intervals_known = True                        # tablo GERÇEKTEN alındı: varsayılan artık doğrulanmıştır
        self._intervals_at = now

    def hours_for(self, symbol: str) -> tuple[int, ...]:
        """Sembolün settlement saatleri.

        Sapma tablosu HİÇ alınamadıysa BOŞ demet döner: sekiz saat varsayımı DOĞRULANMADAN yayılmaz (karşıt
        doğrulama bulgusu — aksi hâlde eksik settlement'lar hiç "due" olmaz ve kapsama yanlışlıkla TAM görünür).
        Tablo alındıysa: listede olmayan sembol varsayılandadır; listede ama aralığı çözülemiyorsa BOŞ demet."""
        with self._lock:
            self._refresh_intervals()
            if not self._intervals_known:
                return ()
            raw = str(symbol).replace("/", "")
            if raw not in self._intervals:
                return FUNDING_HOURS_UTC
            return self._intervals[raw] or ()

    # ------------------------------------------------------------------ gerçekleşmiş oranlar
    def _ensure(self, symbol: str, want_ms: int) -> None:
        now = int(self.clock_ms())
        cov = self._covered.get(symbol)
        fresh = self._fetched_at.get(symbol) is not None and (now - self._fetched_at[symbol]) < self.rate_ttl_s * 1000
        if cov is not None and cov[0] <= want_ms <= cov[1] and fresh:
            return
        start = min(int(want_ms) - MATCH_TOLERANCE_MS, now - self.lookback_ms)
        end = now + MATCH_TOLERANCE_MS
        self.stats["history_calls"] += 1
        try:
            rows = self.provider.funding_history(symbol, limit=1000, start_ms=start, end_ms=end) or []
        except Exception as exc:  # noqa: BLE001 — ağ arızası tahakkuku BEKLETİR, çökertmez
            self.stats["history_errors"] += 1
            log.warning("%s funding geçmişi alınamadı: %s", symbol, exc)
            return
        tbl = self._rates.setdefault(symbol, {})
        for r in rows:
            if not isinstance(r, dict):
                continue
            ts, rate = r.get("funding_ts"), r.get("rate")
            if rate is None:
                continue
            try:
                tbl[int(ts)] = D(rate)
            except (TypeError, ValueError, ArithmeticError):
                continue
        prev = self._covered.get(symbol)
        self._covered[symbol] = (min(start, prev[0]) if prev else start, max(end, prev[1]) if prev else end)
        self._fetched_at[symbol] = now

    def lookup(self, symbol: str, when: datetime) -> Decimal | None:
        """`RateLookup` sözleşmesi: settlement anının GERÇEKLEŞMİŞ oranı ya da None. Tahmin ÜRETMEZ."""
        want = int(when.timestamp() * 1000)
        with self._lock:
            self.stats["lookups"] += 1
            self._ensure(symbol, want)
            tbl = self._rates.get(symbol) or {}
            hit = tbl.get(want)
            if hit is None:
                near = [t for t in tbl if abs(t - want) <= MATCH_TOLERANCE_MS]
                hit = tbl[min(near, key=lambda t: abs(t - want))] if near else None
            self.stats["hits" if hit is not None else "misses"] += 1
            return hit

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


__all__ = ["MATCH_TOLERANCE_MS", "DEFAULT_LOOKBACK_MS", "FAILED_INFO_RETRY_S", "FundingRates", "hours_from_interval"]
