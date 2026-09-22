# -*- coding: utf-8 -*-
"""TARAYICI — dönen kuyruk, önceliklendirme, kapsama ve gecikme ölçümü; arka planda çalışır.

Öncelik sırası (her turda yeniden kurulur):
  1. Defterin AÇIK pozisyonları (yönetim: zaman stopu, bar uçları, kural dışı çıkış) — her turda.
  2. Tetik bekleyen / tetiklenmiş planı olan semboller — her turda.
  3. Yeni listelenenler (futures ilk işlem yaşı <= 30 gün, `universe.PRIORITY_MAX_AGE_H`) — en eski taranan önce.
  4. Kalan uygun evren — en eski taranan önce (dönen kuyruk).
Öncelik risk sınırını ya da sinyal şartını GEVŞETMEZ; yalnız sıradır. Hacim sıralaması kuyruğu tüketmez: 3. ve 4.
gruplar `last_scan` yaşına göre döner, böylece hacimde geride olan bir coin sonsuza kadar taranmadan kalmaz.

Bütçe: tur başına `max_symbols_per_cycle` sembol; istek ağırlığı `HttpClient`/`RateBudget` tarafından zaten sınırlıdır
(429/418 → soğuma + üstel geri çekilme). Kapsama (`coverage`), gecikme (`staleness`), kuyruk derinliği ve hata sayacı
durum dosyasına yazılır. Tarama AYRI iş parçacığındadır: ana turu ve 60 sn'lik çıkış izleyicisini BEKLETMEZ.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ..core import iso, utc_now
from .data import TIMEFRAMES
from .universe import PRIORITY_MAX_AGE_H, discover

log = logging.getLogger(__name__)
DEFAULT_CYCLE_S = 60.0
#: Tur bütçesinin (açık pozisyonlar düşüldükten sonra) en az bu oranı yeni listelenenlere/rotasyona ayrılır.
ROTATION_RESERVE = 0.5


class PatternScanner:
    """Evren keşfi + dönen tarama kuyruğu. `book`: `PatternBook`; `data`: `DataService`; `price`: `PriceService`."""

    def __init__(self, *, book: Any, data: Any, price: Any, universe_provider: Any, state_path: Any,
                 min_quote_volume_24h: float, max_spread_pct: float, max_symbols_per_cycle: int = 40,
                 universe_refresh_minutes: float = 30.0, cycle_seconds: float = DEFAULT_CYCLE_S,
                 clock: Callable[[], float] = time.time, run_id: Callable[[], str] | None = None,
                 funding: Any = None) -> None:
        self.book, self.data, self.price = book, data, price
        self.universe_provider = universe_provider
        # FUNDING (2026-09-17): gerçekleşmiş settlement oranları hem bar uygulamasına hem çıkış izleyicisine
        # BAĞLANIR. Verilmezse evren sağlayıcısından kurulur; o da veremiyorsa None kalır ve defter dönemleri
        # BEKLETİR (bilinmeyen funding sıfır maliyet SAYILMAZ). Defter de aynı nesneyi kullanır (tek kaynak).
        if funding is None and universe_provider is not None and hasattr(universe_provider, "funding_history"):
            from .funding import FundingRates
            funding = FundingRates(universe_provider, clock_ms=lambda: int(clock() * 1000))
        self.funding = funding
        if funding is not None and hasattr(book, "bind_funding"):
            book.bind_funding(funding)
        self.state_path = state_path
        self.min_quote_volume_24h = float(min_quote_volume_24h)
        self.max_spread_pct = float(max_spread_pct)
        self.max_symbols_per_cycle = int(max_symbols_per_cycle)
        self.universe_refresh_s = float(universe_refresh_minutes) * 60.0
        self.cycle_seconds = float(cycle_seconds)
        self.clock = clock
        self._run_id = run_id or (lambda: "")
        self.universe: dict[str, Any] = {}
        self.last_universe_at: float | None = None
        self.last_cycle_at: float | None = None
        self.last_error: str = ""
        self.cycles = 0
        self.symbols_scanned = 0
        self.errors = 0
        self._last_scan_ms: dict[str, int] = {}
        self._cycle_report: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ evren
    def _now_dt(self):
        """Enjekte edilen saate bağlı UTC an — `utc_now()` DEĞİL: testler ve replay kendi saatlerini verir,
        fiyat yaşı/bar kapanışı bu ana göre denetlenir (duvar saati sızmaz)."""
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc)

    def refresh_universe(self, *, now_ms: int | None = None, force: bool = False) -> dict[str, Any]:
        now_ms = int(now_ms if now_ms is not None else self.clock() * 1000)
        if not force and self.last_universe_at is not None and (self.clock() - self.last_universe_at) < self.universe_refresh_s:
            return self.universe
        snap = discover(self.universe_provider, now_ms=now_ms, min_quote_volume_24h=self.min_quote_volume_24h,
                        max_spread_pct=self.max_spread_pct, previous=self.universe or None)
        if snap.get("ok") is False and self.universe:
            self.last_error = str(snap.get("error") or "")[:200]
            log.warning("formasyon evreni yenilenemedi (eski evren korunuyor): %s", self.last_error)
            return self.universe
        self.universe = snap
        self.last_universe_at = self.clock()
        ch = snap.get("changes") or {}
        for e in (ch.get("new_listings") or [])[:20]:
            self.book._event("NEW_LISTING", e["symbol"], str(e.get("cohort") or ""), self._now_dt(), age_h=e.get("age_h"), first_trade=e.get("futures_first_trade"))
        for e in (ch.get("delisted") or [])[:20]:
            self.book._event("DELISTED", e["symbol"], "NOT_IN_EXCHANGE_INFO", self._now_dt())
        for e in (ch.get("status_changes") or [])[:20]:
            self.book._event("STATUS_CHANGE", e["symbol"], "%s->%s" % (e.get("from"), e.get("to")), self._now_dt())
        try:
            from ..core import atomic_write_json
            atomic_write_json(self.state_path / "pattern_universe.json", snap)
        except Exception as exc:  # noqa: BLE001 — kayıt hatası taramayı durdurmaz
            log.warning("formasyon evreni yazılamadı: %s", exc)
        return snap

    # ------------------------------------------------------------------ kuyruk
    def queue(self, *, now_ms: int) -> list[tuple[str, str]]:
        """(sembol, öncelik_grubu) listesi — öncelik sırasıyla, tekrar yok."""
        entries: dict[str, dict] = dict((self.universe or {}).get("entries") or {})
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(sym: str, group: str) -> None:
            if sym and sym not in seen:
                seen.add(sym)
                out.append((sym, group))

        for sym in list(self.book.ledger.positions):
            add(sym, "open_position")
        from .strategy import PL_AWAITING, PL_TRIGGERED
        for pl in self.book.plans.values():
            if pl.get("status") in (PL_AWAITING, PL_TRIGGERED):
                add(str(pl.get("symbol") or ""), "pending_plan")
        elig = [(s, e) for s, e in entries.items() if e.get("eligible")]
        new_list = [(s, e) for s, e in elig if e.get("priority")]
        rest = [(s, e) for s, e in elig if not e.get("priority")]
        new_list.sort(key=lambda t: (self._last_scan_ms.get(t[0], 0), t[1].get("age_h") if t[1].get("age_h") is not None else 1e9))
        rest.sort(key=lambda t: (self._last_scan_ms.get(t[0], 0), -(t[1].get("quote_volume_24h") or 0.0)))
        for s, _ in new_list:
            add(s, "new_listing")
        for s, _ in rest:
            add(s, "rotation")
        return out

    def select_batch(self, q: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
        """Bütçe dağıtımı. Açık pozisyonlar HER TUR taranır (yönetim). Bekleyen planlar kuyruğu AÇLIĞA DÜŞÜREMEZ:
        bütçenin en az `ROTATION_RESERVE` oranı yeni listelenenlere/rotasyona ayrılır — aksi hâlde plan üreten ilk
        semboller sonsuza kadar öne geçer ve evrenin kalanı hiç taranmaz (ölçüldü: 14 sembolün 9'u hiç taranmadı)."""
        budget = max(1, int(budget))
        pos = [x for x in q if x[1] == "open_position"][:budget]
        rest_budget = budget - len(pos)
        if rest_budget <= 0:
            return pos
        reserve = max(1, int(round(rest_budget * ROTATION_RESERVE)))
        pend = [x for x in q if x[1] == "pending_plan"][:max(0, rest_budget - reserve)]
        rot = [x for x in q if x[1] in ("new_listing", "rotation")][:rest_budget - len(pend)]
        out = pos + pend + rot
        if len(out) < budget:                          # rezerv doldurulamadıysa kalan bütçe bekleyen planlara döner
            extra = [x for x in q if x not in out][:budget - len(out)]
            out += extra
        return out

    # ------------------------------------------------------------------ tek tur
    def scan_cycle(self, *, now_ms: int | None = None, limit: int | None = None) -> dict[str, Any]:
        """Bir tarama turu: kuyruktan bütçe kadar sembol. Bir sembolün arızası turu DURDURMAZ."""
        now_ms = int(now_ms if now_ms is not None else self.clock() * 1000)
        self.refresh_universe(now_ms=now_ms)
        # FUNDING AĞ ADIMI (2026-09-22, REVIEW-2026-09-22 F2): defterin tick'i funding'i YALNIZ bellekten okur; aralık
        # tablosu ve gerçekleşmiş oranlar burada — tarayıcı iş parçacığında, defter kilidi ve 60 sn çıkış izleyicisi
        # DIŞINDA — çekilir. Ardından kapanmış işlemlerin bekleyen funding'i bellekten uzlaştırılır.
        self.funding_step()
        entries: dict[str, dict] = dict((self.universe or {}).get("entries") or {})
        q = self.queue(now_ms=now_ms)
        budget = int(limit if limit is not None else self.max_symbols_per_cycle)
        batch = self.select_batch(q, budget)
        t0 = self.clock()
        totals = {"scanned": 0, "findings": 0, "confirmed": 0, "plans": 0, "triggered": 0, "opened": 0, "errors": 0}
        by_group: dict[str, int] = {}
        details: list[dict[str, Any]] = []
        run_id = self._run_id() or ""
        for sym, group in batch:
            by_group[group] = by_group.get(group, 0) + 1
            try:
                res = self._scan_symbol(sym, entries.get(sym), now_ms=now_ms, run_id=run_id)
            except Exception as exc:  # noqa: BLE001 — tek sembol arızası kuyruğu DURDURMAZ
                self.errors += 1
                totals["errors"] += 1
                self.last_error = f"{sym}: {type(exc).__name__}: {exc}"[:200]
                log.warning("formasyon taraması başarısız (%s): %s", sym, exc)
                self._last_scan_ms[sym] = now_ms
                continue
            self._last_scan_ms[sym] = now_ms
            self.symbols_scanned += 1
            totals["scanned"] += 1
            for k in ("findings", "confirmed", "plans", "triggered", "opened"):
                totals[k] += int(res.get({"findings": "new_findings", "plans": "new_plans"}.get(k, k)) or 0)
            if res.get("new_findings") or res.get("new_plans") or res.get("triggered") or res.get("opened") or res.get("rejected"):
                details.append({"symbol": sym, "group": group, **{k: res.get(k) for k in ("new_findings", "confirmed", "new_plans", "triggered", "opened", "rejected")}})
        self.cycles += 1
        self.last_cycle_at = self.clock()
        elapsed = self.last_cycle_at - t0
        try:
            self.book.save(self._position_marks(now_ms), self._now_dt())
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon defteri özeti yazılamadı: %s", exc)
        self._cycle_report = {"at": iso(self._now_dt()), "as_of_ms": now_ms, "elapsed_s": round(elapsed, 2), "budget": budget,
                              "queue_depth": len(q), "batch": len(batch), "by_group": by_group, "totals": totals,
                              "details": details[:40], "run_id": run_id}
        try:
            from ..core import atomic_write_json
            atomic_write_json(self.state_path / "pattern_scan.json", {"schema_version": "pattern_scan_v1", **self.status()})
        except Exception as exc:  # noqa: BLE001
            log.warning("formasyon tarama durumu yazılamadı: %s", exc)
        return self._cycle_report

    def funding_step(self) -> dict[str, Any]:
        """Ağdan funding verisi çek (defter kilidi TUTULMADAN) + kapanmış işlemlerin bekleyen funding'ini uzlaştır.
        Arıza turu DURDURMAZ; koruyucu çıkış bu adımı hiç BEKLEMEZ (`exit_check` onu çağırmaz)."""
        out: dict[str, Any] = {}
        if self.funding is None or not hasattr(self.funding, "refresh"):
            return out
        try:
            syms = self.book.pending_funding_symbols() if hasattr(self.book, "pending_funding_symbols") else list(self.book.ledger.positions)
            out["refresh"] = self.funding.refresh(syms)
        except Exception as exc:  # noqa: BLE001
            log.warning("funding verisi tazelenemedi (tur sürer): %s", exc)
        try:
            if hasattr(self.book, "reconcile_funding"):
                out["late_posted"] = len(self.book.reconcile_funding(self._now_dt()))
        except Exception as exc:  # noqa: BLE001
            log.warning("geç funding uzlaştırması başarısız (tur sürer): %s", exc)
        return out

    def _position_marks(self, now_ms: int) -> dict[str, float]:
        syms = list(self.book.ledger.positions)
        if not syms:
            return {}
        _, marks_f, _ = self.price.marks(syms, now_ms=now_ms)
        return marks_f

    def _scan_symbol(self, symbol: str, entry: dict[str, Any] | None, *, now_ms: int, run_id: str) -> dict[str, Any]:
        bars: dict[str, list] = {}
        statuses: dict[str, dict] = {}
        for tf in TIMEFRAMES:
            rows, st = self.data.bars(symbol, tf, as_of_ms=now_ms)
            bars[tf], statuses[tf] = rows, st
        price = self.price.mark(symbol, now_ms=now_ms)
        # KARAR ANI: `now_ms` turun REFERANSIDIR (bar kapanışı/güncellik); uzun bir turda sembol sırası geldiğinde
        # dakikalarca eski olabilir. Geçerlilik süresi bu sembolün GERÇEK tarama anıyla denetlenir.
        res = self.book.process_symbol(symbol, bars_by_tf=bars, statuses=statuses, as_of_ms=now_ms, universe_entry=entry,
                                       price=price if price.get("ok") else price, liquidity=lambda: self.price.liquidity(symbol),
                                       run_id=run_id, decision_ms=int(self.clock() * 1000))
        # açık pozisyon: kapanmış 15m bar uçları (girişten sonra, bir kez) — T2/M2 ile AYNI sözleşme.
        # Piyasa kimliği ve funding oranları bar uygulamasına BAĞLANIR (fiyat bandından tahmin YOK, funding=0 varsayımı YOK).
        if symbol in self.book.ledger.positions and bars.get("15m"):
            rows = bars["15m"][-48:]
            # Piyasa kimliği çerçevenin KENDİ provenansından gelir; yoksa `""` taşınır ve bar kapısı REDDEDER
            # (varsayılan "USDM_PERP" damgalamak kapıyı anlamsız kılardı — karşıt doğrulama bulgusu).
            spec = {"tf": "15m", "rows": rows, "mark": float(price.get("mark") or 0.0),
                    "market": str((statuses.get("15m") or {}).get("market") or ""),
                    "source": (statuses.get("15m") or {}).get("source"),
                    "first_bar_ms": int(rows[0]["timestamp"]) if rows else 0}
            self.book.apply_closed_bars({symbol: spec}, now=self._now_dt(),
                                        funding_rate_lookup=self.funding)   # KAYNAK nesnesi: oran + settlement mark (funding_settlement_v2)
        return res

    # ------------------------------------------------------------------ açık pozisyon izleyicisi (tarama dışı)
    def exit_check(self) -> list:
        """Açık pozisyonlar için doğrulanmış güncel fiyatla stop/hedef/likidasyon kontrolü. Tarama kuyruğundan
        BAĞIMSIZ: ana döngünün 60 sn'lik çıkış izleyicisi bunu çağırır ve tarama iş parçacığını BEKLEMEZ."""
        book = self.book
        if not book.ledger.positions:
            return []
        now = self._now_dt()
        now_ms = int(now.timestamp() * 1000)
        marks, marks_f, gaps = self.price.marks(list(book.ledger.positions), now_ms=now_ms)
        book.record_gaps(gaps, now)
        # Funding oranı BULUNAMAZSA lookup None döner ve defter o dönemi bekletir; koruyucu stop/hedef kontrolü
        # bundan ETKİLENMEZ (tick yine çalışır).
        recs = book.tick(marks, now=now, funding_rate_lookup=self.funding,   # KAYNAK nesnesi (oran + settlement mark)
                         bar_advance=False) if marks else []
        book.save(marks_f, now)
        return recs

    # ------------------------------------------------------------------ durum / arka plan
    def status(self) -> dict[str, Any]:
        u = self.universe or {}
        counts = dict(u.get("counts") or {})
        now_ms = int(self.clock() * 1000)
        scanned_ever = len(self._last_scan_ms)
        elig = [s for s, e in (u.get("entries") or {}).items() if e.get("eligible")]
        ages = [(now_ms - v) / 1000.0 for v in self._last_scan_ms.values()]
        never = [s for s in elig if s not in self._last_scan_ms]
        return {"generated_at": iso(self._now_dt()), "enabled": True, "cycles": self.cycles, "symbols_scanned": self.symbols_scanned,
                "errors": self.errors, "last_error": self.last_error, "cycle_seconds": self.cycle_seconds,
                "max_symbols_per_cycle": self.max_symbols_per_cycle,
                "last_cycle_at": iso_from(self.last_cycle_at), "last_universe_at": iso_from(self.last_universe_at),
                "universe": {"generated_at": u.get("generated_at"), "counts": counts, "policy": u.get("policy"),
                             "changes": {k: len(v) for k, v in (u.get("changes") or {}).items()},
                             "recent_new_listings": list((u.get("changes") or {}).get("new_listings") or [])[:10]},
                "coverage": {"eligible": len(elig), "scanned_at_least_once": sum(1 for s in elig if s in self._last_scan_ms),
                             "never_scanned": len(never), "never_scanned_sample": never[:10], "tracked": scanned_ever,
                             "max_staleness_s": round(max(ages), 1) if ages else None,
                             "median_staleness_s": round(sorted(ages)[len(ages) // 2], 1) if ages else None},
                "last_cycle": dict(self._cycle_report),
                "data_stats": dict(getattr(self.data, "stats", {})), "price_stats": dict(getattr(self.price, "stats", {}))}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pattern-scanner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_cycle()
            except Exception as exc:  # noqa: BLE001 — iş parçacığı ASLA ölmez
                self.errors += 1
                self.last_error = f"loop: {type(exc).__name__}: {exc}"[:200]
                log.exception("formasyon tarama döngüsü hatası: %s", exc)
            waited = 0.0
            while waited < self.cycle_seconds and not self._stop.is_set():
                self._stop.wait(min(2.0, self.cycle_seconds - waited))
                waited += 2.0


def iso_from(t: float | None) -> str | None:
    if t is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="seconds")


__all__ = ["DEFAULT_CYCLE_S", "ROTATION_RESERVE", "PRIORITY_MAX_AGE_H", "PatternScanner", "iso_from"]
