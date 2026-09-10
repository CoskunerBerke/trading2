"""Gerçek per-settlement funding oranı önbelleği (USDⓈ-M `fundingRate` geçmişi).

FUNDING SETTLEMENT V1 (2026-09-09). Canlı tur yolu funding'i TEK bir anlık oranla (`premiumIndex`
üzerinden gelen `lastFundingRate`, ki o SONRAKİ dönem için tahmindir) ve TEK bir anlık mark'la
tahakkuk ettiriyordu; kaçırılan 15 settlement'ın hepsi aynı oranı alıyordu. Üretim defterindeki 29
kapanmış işlemin gerçek `fundingRate` geçmişiyle uzlaştırılması: kayıtlı +0,067382 USDT, gerçek
+0,004014 USDT (mutlak hata toplamı 0,209232 USDT). Offline gap yolu (`ops/gap.py`) bu işi ZATEN
doğru yapıyordu — kusur yalnız canlı turdaydı.

Sözleşme:
  * `lookup(symbol, settlement_dt)` SAF BELLEK okumasıdır — ağ ÇAĞRISI YAPMAZ, istisna atmaz,
    bilinmiyorsa None döner. Doğrudan `FundingSchedule.accrue`'nun `rate_lookup`'u olarak verilebilir.
  * Ağ yalnız `ensure()` ile ve YALNIZ önbellekte olmayan, vadesi gelmiş settlement'lar için açılır.
    Settlement'lar 8 saatte bir olduğundan bu, açık pozisyon başına ~8 saatte 1 istek demektir.
  * Arıza ESKİ önbelleği korur ve `retry_cooldown_s` boyunca aynı sembol yeniden denenmez → venue
    erişilemezken tur BLOKLANMAZ. Bu durumda `lookup` None döner, çağıran zinciri (bkz.
    `accounting.funding.chained_rates`) anlık orana düşer, yani davranış ESKİSİYLE birebir aynıdır.
  * Yazım atomiktir (geçici dosya + `os.replace`); `max_age_days`'ten eski kayıtlar budanır.

Anahtarlama: settlement zamanı EN YAKIN saate yuvarlanmış epoch-saat. `ops/gap.py` epoch-saat
kullanıyor (taban alarak); yuvarlama, venue'nun `fundingTime` değerini saat sınırının birkaç ms
altında dönmesi durumunda da doğru kovaya düşmeyi garanti eder.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..accounting.funding import FundingQuote
from .providers import to_raw

log = logging.getLogger(__name__)

SCHEMA_VERSION = "funding_rates_v1"
_HOUR_MS = 3_600_000
SOURCE = "funding_history"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def hour_key(when: datetime | int | float) -> int:
    """Settlement zamanı → EN YAKIN saate yuvarlanmış epoch-saat anahtarı."""
    if isinstance(when, datetime):
        dt = when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
        ms = int(dt.timestamp() * 1000)
    else:
        ms = int(when)
    return (ms + _HOUR_MS // 2) // _HOUR_MS


def _dec(x: Any) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        d = Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


class FundingRateCache:
    """`{raw_symbol: {epoch_saat: [rate, mark]}}` — settlement'ın GERÇEK oranı ve o andaki mark'ı."""

    def __init__(self, path: Path | str, *, max_age_days: int = 30, retry_cooldown_s: float = 60.0,
                 error_cooldown_s: float | None = None, max_symbols_per_refresh: int = 8, fetch_limit: int = 1000):
        self.path = Path(path)
        self.max_age_days = max(1, int(max_age_days))
        # Kısa soğuma: kayıt HENÜZ yayımlanmamış olabilir (settlement'tan saniyeler sonra) → yakında yeniden dene.
        self.retry_cooldown_s = max(0.0, float(retry_cooldown_s))
        # Uzun soğuma: venue ERİŞİLEMEZ (istisna) → her turda 3 sembol x zaman aşımı beklemeyelim.
        self.error_cooldown_s = max(self.retry_cooldown_s, 300.0) if error_cooldown_s is None else max(0.0, float(error_cooldown_s))
        self.max_symbols_per_refresh = max(1, int(max_symbols_per_refresh))
        self.fetch_limit = max(1, int(fetch_limit))
        self._rates: dict[str, dict[int, tuple[str, str]]] = {}
        self._retry_after: dict[str, float] = {}
        #: sembol -> BASARIYLA cekilmis pencerenin sonu (ms). Kalici; `load`/`save` tasir.
        self._covered_to: dict[str, int] = {}
        self.fetched_at: dict[str, str] = {}
        self.stats = {"hits": 0, "misses": 0, "fetches": 0, "fetch_errors": 0, "rows": 0}
        self.load()

    # ------------------------------------------------------------------ durum
    @property
    def size(self) -> int:
        return sum(len(t) for t in self._rates.values())

    def symbols(self) -> list[str]:
        return sorted(self._rates)

    # ------------------------------------------------------------------ okuma (SAF BELLEK)
    def get(self, symbol: str, when: datetime) -> FundingQuote | None:
        table = self._rates.get(to_raw(symbol))
        if not table:
            return None
        row = table.get(hour_key(when))
        if not row:
            return None
        rate = _dec(row[0])
        if rate is None:
            return None
        mark = _dec(row[1] if len(row) > 1 else None)
        # Venue kaydi: TAM O settlement zaman damgasi icin alindi -> DOGRULANMIS.
        return FundingQuote(rate=rate, mark=mark if (mark is not None and mark > 0) else None,
                            source=SOURCE, verified=True)

    def lookup(self, symbol: str, when: datetime) -> FundingQuote | None:
        """`RateLookup` uyumlu; ağ yok, istisna yok. Bilinmiyorsa None (çağıran yedeğe düşer)."""
        q = self.get(symbol, when)
        self.stats["hits" if q is not None else "misses"] += 1
        return q

    def missing(self, symbol: str, times: Iterable[datetime]) -> list[datetime]:
        return [t for t in times if self.get(symbol, t) is None]

    def settlements_in(self, symbol: str, start: datetime, end: datetime) -> list[datetime]:
        """(start, end] araligindaki GERCEK venue settlement zamanlari.

        Sabit 00/08/16 grid'i YOKTUR: venue hangi zamanlarda funding yayimladiysa onlar donulur.
        4 saatlik ve 1 saatlik sozlesmeler bu yolla dogru sayida donem uretir. Onbellekte kayit
        yoksa BOS liste doner ve `accrue` fail-closed davranir (dönem BEKLER, kaybolmaz).
        """
        table = self._rates.get(to_raw(symbol))
        if not table:
            return []
        # Tablo anahtari EPOCH-SAAT indeksidir (bkz. `hour_key`), ms DEGIL.
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        return [datetime.fromtimestamp(k * _HOUR_MS / 1000, tz=timezone.utc)
                for k in sorted(table) if lo < k * _HOUR_MS <= hi]

    def covered_to_ms(self, symbol: str) -> int:
        """Bu sembol icin BASARIYLA cekilmis pencerenin sonu (ms). Hic cekim yoksa 0."""
        return int(self._covered_to.get(to_raw(symbol), 0))

    def needs_window(self, symbol: str, start: datetime, end: datetime,
                     *, min_interval_h: int = 1, grace_s: int = 60) -> bool:
        """Bu pencere icin ag cagrisi GEREKLI mi?

        Iki kosul birden: (a) pencere en kisa venue funding araligindan (1 saat) daha uzun —
        yani icinde settlement OLABILIR; (b) daha once basariyla cekilmis kapsama penceresi
        pencerenin sonunu ortmuyor. Boylece 8 saatlik bir sembol icin ~8 saatte bir, 1 saatlik
        bir sembol icin ~saatte bir istek atilir; kapsanan turlarda HIC istek atilmaz.
        """
        if (end - start).total_seconds() < min_interval_h * 3600:
            return False
        return self.covered_to_ms(symbol) < int(end.timestamp() * 1000) - grace_s * 1000

    # ------------------------------------------------------------------ kalıcılık
    def load(self) -> None:
        try:
            if not self.path.exists():
                return
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("funding_rates okunamadı (%s): %s — önbellek BOŞ sayılıyor", self.path, exc)
            return
        cov = d.get("covered_to_ms")
        if isinstance(cov, dict):
            for k, v in cov.items():
                try:
                    self._covered_to[to_raw(str(k))] = int(v)
                except (TypeError, ValueError):
                    continue
        rates = d.get("rates")
        if not isinstance(rates, dict):
            return
        out: dict[str, dict[int, tuple[str, str]]] = {}
        for sym, table in rates.items():
            if not isinstance(table, dict):
                continue
            rows: dict[int, tuple[str, str]] = {}
            for k, v in table.items():
                try:
                    key = int(k)
                except (TypeError, ValueError):
                    continue
                if isinstance(v, (list, tuple)) and v:
                    rows[key] = (str(v[0]), str(v[1]) if len(v) > 1 and v[1] is not None else "")
                elif isinstance(v, (str, int, float)):
                    rows[key] = (str(v), "")
            if rows:
                out[str(sym).upper()] = rows
        self._rates = out
        fa = d.get("fetched_at")
        if isinstance(fa, dict):
            self.fetched_at = {str(k).upper(): str(v) for k, v in fa.items()}

    def _prune(self, now: float) -> int:
        floor_key = hour_key(int((now - self.max_age_days * 86400) * 1000))
        dropped = 0
        for sym in list(self._rates):
            table = self._rates[sym]
            old = [k for k in table if k < floor_key]
            for k in old:
                del table[k]
            dropped += len(old)
            if not table:
                del self._rates[sym]
                self.fetched_at.pop(sym, None)
        return dropped

    def save(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        dropped = self._prune(now)
        doc = {"schema_version": SCHEMA_VERSION, "saved_at": _iso(now), "n": self.size,
               "fetched_at": dict(sorted(self.fetched_at.items())),
               "covered_to_ms": dict(sorted(self._covered_to.items())),
               "rates": {sym: {str(k): [r, m] for k, (r, m) in sorted(table.items())}
                         for sym, table in sorted(self._rates.items())}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("funding_rates yazılamadı (%s): %s — bellekte kullanılıyor", self.path, exc)
            return {"persisted": False, "error": str(exc), "n": self.size, "pruned": dropped}
        return {"persisted": True, "n": self.size, "pruned": dropped}

    # ------------------------------------------------------------------ yenileme (AĞ)
    def in_cooldown(self, symbol: str, now: float | None = None) -> bool:
        t = self._retry_after.get(to_raw(symbol))
        if t is None:
            return False
        return (time.time() if now is None else now) < t

    def _cool(self, raw: str, now: float, seconds: float) -> None:
        self._retry_after[raw] = now + seconds

    def refresh(self, provider: Any, symbol: str, start: datetime, end: datetime, *, now: float | None = None) -> dict:
        """`provider.funding_history` → önbellek. Arıza/boş sonuç ESKİ kayıtları EZMEZ, cooldown başlatır."""
        now = time.time() if now is None else now
        raw = to_raw(symbol)
        start_ms = int(start.timestamp() * 1000) - _HOUR_MS      # pencere başındaki settlement de kapsansın
        end_ms = int(end.timestamp() * 1000) + _HOUR_MS
        self.stats["fetches"] += 1
        try:
            rows = provider.funding_history(symbol, limit=self.fetch_limit, start_ms=start_ms, end_ms=end_ms) or []
        except Exception as exc:  # noqa: BLE001 — arıza turu düşürmez; lookup None döner, çağıran yedeğe düşer
            self.stats["fetch_errors"] += 1
            self._cool(raw, now, self.error_cooldown_s)
            log.warning("%s funding geçmişi alınamadı (%s: %s) — önbellek korunuyor, %.0fs soğuma",
                        symbol, type(exc).__name__, exc, self.error_cooldown_s)
            return {"ok": False, "symbol": raw, "error": f"{type(exc).__name__}: {exc}", "n": 0}
        table = self._rates.setdefault(raw, {})
        added = 0
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            fts = int(r.get("funding_ts") or 0)
            rate = _dec(r.get("rate"))
            if not fts or rate is None:
                continue
            mark = _dec(r.get("mark"))
            table[hour_key(fts)] = (format(rate, "f"), format(mark, "f") if (mark is not None and mark > 0) else "")
            added += 1
        self.stats["rows"] += added
        if not table:
            del self._rates[raw]
        # Basarili cekim -> pencere KAPSANDI. Bos sonuc da kapsamadir: venue o aralikta kayit
        # yayimlamamis demektir; her turda ayni soruyu tekrar sormanin anlami yok.
        self._covered_to[raw] = max(self._covered_to.get(raw, 0), int(end.timestamp() * 1000))
        if added == 0:
            # Boş geçmiş: kayıt henüz yayımlanmamış ya da sembolde funding yok. Her turda yeniden
            # sormamak için KISA soğuma başlatılır; bu bir HATA değildir, `ok` True kalır.
            self._cool(raw, now, self.retry_cooldown_s)
            return {"ok": True, "symbol": raw, "n": 0, "empty": True}
        self._retry_after.pop(raw, None)
        self.fetched_at[raw] = _iso(now)
        return {"ok": True, "symbol": raw, "n": added}

    def ensure_window(self, provider_factory: Callable[[], Any],
                      needs: Mapping[str, "tuple[datetime, datetime]"],
                      *, now: float | None = None, save: bool = True,
                      min_interval_h: int = 1) -> dict:
        """PENCERE tabanli yenileme — settlement GRID'i varsaymaz (D2).

        `needs`: sembol -> (pencere_baslangici, pencere_sonu). Yalnizca `needs_window` True olan
        semboller icin aga cikilir; venue o pencerede hangi settlement'lari yayimladiysa onbellege
        girer ve `settlements_in` onlari dondurur.
        """
        now = time.time() if now is None else now
        todo: dict[str, tuple[datetime, datetime]] = {}
        cooling: list[str] = []
        for sym, win in (needs or {}).items():
            start, end = win
            if not self.needs_window(sym, start, end, min_interval_h=min_interval_h):
                continue
            if self.in_cooldown(sym, now):
                cooling.append(to_raw(sym))
                continue
            todo[sym] = (start, end)
        if not todo:
            return {"ok": True, "skipped": "kapsaniyor", "fetched": 0, "cooldown": sorted(cooling), "n": self.size}
        try:
            provider = provider_factory()
        except Exception as exc:  # noqa: BLE001 — saglayici acilamadi: tur DUSMEZ, onbellek korunur
            self.stats["fetch_errors"] += 1
            log.warning("funding saglayicisi acilamadi: %s: %s — onbellek korunuyor", type(exc).__name__, exc)
            return {"ok": False, "error": f"saglayici acilamadi: {type(exc).__name__}: {exc}", "fetched": 0, "n": self.size}
        results = []
        for sym in sorted(todo)[:self.max_symbols_per_refresh]:
            lo, hi = todo[sym]
            results.append(self.refresh(provider, sym, lo, hi, now=now))
        added = sum(int(r.get("n") or 0) for r in results)
        out = {"ok": all(r.get("ok") for r in results), "fetched": len(results), "added": added,
               "cooldown": sorted(cooling), "results": results, "n": self.size}
        if save:
            out["persist"] = self.save(now=now)
        return out

    def ensure(self, provider_factory: Callable[[], Any], needs: Mapping[str, Iterable[datetime]],
               *, now: float | None = None, save: bool = True) -> dict:
        """`needs`: sembol → çözülmesi gereken settlement zamanları.

        Yalnız ÖNBELLEKTE OLMAYANLAR için ağa çıkar; hiç eksik yoksa sağlayıcı bile yaratılmaz.
        Sağlayıcı yaratılamazsa ya da tek tek çekimler patlarsa tur DÜŞMEZ: eski önbellek korunur.
        """
        now = time.time() if now is None else now
        todo: dict[str, tuple[datetime, datetime]] = {}
        cooling: list[str] = []
        for sym, times in (needs or {}).items():
            miss = self.missing(sym, times)
            if not miss:
                continue
            if self.in_cooldown(sym, now):
                cooling.append(to_raw(sym))
                continue
            todo[sym] = (min(miss), max(miss))
        if not todo:
            return {"ok": True, "skipped": "kapsanıyor", "fetched": 0, "cooldown": sorted(cooling), "n": self.size}
        try:
            provider = provider_factory()
        except Exception as exc:  # noqa: BLE001
            self.stats["fetch_errors"] += 1
            log.warning("funding sağlayıcısı açılamadı: %s: %s — önbellek korunuyor", type(exc).__name__, exc)
            return {"ok": False, "error": f"sağlayıcı açılamadı: {type(exc).__name__}: {exc}", "fetched": 0, "n": self.size}
        results = []
        for sym in sorted(todo)[:self.max_symbols_per_refresh]:
            lo, hi = todo[sym]
            r = self.refresh(provider, sym, lo, hi, now=now)
            # Çekim başarılı ama İSTENEN dönem hâlâ yoksa (venue o kaydı hiç vermiyor) turdan tura
            # aynı isteği tekrarlamayalım: kısa soğuma. `lookup` None kalır → çağıran yedeğe düşer.
            if r.get("ok") and self.missing(sym, needs[sym]):
                self._cool(to_raw(sym), now, self.retry_cooldown_s)
                r["still_missing"] = True
            results.append(r)
        added = sum(int(r.get("n") or 0) for r in results)
        out = {"ok": all(r.get("ok") for r in results), "fetched": len(results), "added": added,
               "cooldown": sorted(cooling), "results": results, "n": self.size}
        if save and added:
            out["persist"] = self.save(now=now)
        return out


__all__ = ["SCHEMA_VERSION", "SOURCE", "FundingRateCache", "hour_key"]
