"""BOX ZAMANLAYICISI (2026-09-23) — Box defterinin kapanmış 5m mumlarını ANA TURDAN BAĞIMSIZ, zamanında değerlendirir.

Sorun (ölçüldü): ana tur tek iş parçacıklıdır (üretimde 1414 sn / 41 sembol) ve `watch` döngüsü tur → 15 dk bekleme
sırasıyla ilerler; Box kuralı 5m mumlarını yalnız tur anında görüyordu. 5m kaydı 2 bar (~10 dk) taze sayıldığından tur
anına denk gelmeyen teyitler kaçıyordu (REVIEW-2026-09-23 §10). Tazelik eşiği GEVŞETİLMEDİ: değerlendirme her 5m
kapanışından hemen sonra, bu modülün kendi iş parçacığında yapılır.

Sözleşme:
* Her yeni kapanmış 5m mumu EN FAZLA BİR KEZ değerlendirilir (`last_bar_open`, yeniden başlatmada korunur) ve aynı sinyal
  mumundan ikinci giriş açılmaz: defterin giriş kaydında aynı sembol + `signal_ts` varsa sembol o mumda değerlendirilmez.
* Veri kimliği kâğıt defterlerle AYNI: USDⓈ-M perp mumları, doğrulanmış perp mark, bu değerlendirmeye bağlı provenans
  (tur kimliği = zamanlayıcı kimliği) ve `verify_paper_data` güncellik denetimi (eşik değişmedi).
* Koruma: her yoklamada (en az `tick_every_s` arayla) açık Box pozisyonları güncel perp mark ile tick'lenir; kapanmış 1h
  bar uçları defterin `apply_closed_bars` sözleşmesiyle uygulanır (açılıştan SONRA açılmış bar, bir kez).
* Arıza iş parçacığını ya da ana turu DURDURMAZ; her değerlendirme `box_timer.json` durum dosyasına yazılır
  (mum kapanışından değerlendirmeye gecikme, kaçan mum sayısı, engellenen yineleme)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .accounting import TickData
from .core import atomic_write_json, iso, read_json

log = logging.getLogger(__name__)

M5_MS = 300_000
H1_MS = 3_600_000
D1_MS = 86_400_000
STATUS_FILE = "box_timer.json"
#: Kapanıştan sonra borsanın mumu yayımlaması için bekleme (ms). Tazelik eşiğiyle ilgisi yoktur.
SETTLE_MS = 3_000
M5_LIMIT = 300            # ~25 saat: günün tamamı + önceki gün kapanışı (Box kuralı günün 5m barlarını okur)
D1_LIMIT = 10


def _now_ms() -> int:
    return int(time.time() * 1000)


class BoxTimer:
    """Box defterinin 5m değerlendirmesi + koruyucu tick'i için arka plan iş parçacığı."""

    def __init__(self, *, book: Any, provider_factory: Callable[[], Any], state_path: Path | str,
                 symbols_fn: Callable[[], list[str]], funding_rates: Any = None,
                 clock_ms: Callable[[], int] = _now_ms, poll_s: float = 15.0, tick_every_s: float = 60.0,
                 settle_ms: int = SETTLE_MS) -> None:
        self.book = book
        self.provider_factory = provider_factory
        self.symbols_fn = symbols_fn
        self.funding_rates = funding_rates
        self.clock_ms = clock_ms
        self.poll_s = float(poll_s)
        self.tick_every_s = float(tick_every_s)
        self.settle_ms = int(settle_ms)
        self.status_path = Path(state_path) / STATUS_FILE
        prev = read_json(self.status_path, default=None) or {}
        self.last_bar_open = int(prev.get("last_bar_open") or 0)
        self.evaluations = int(prev.get("evaluations") or 0)
        self.missed_bars = int(prev.get("missed_bars") or 0)
        self.duplicates_blocked = int(prev.get("duplicates_blocked") or 0)
        self.lags_s: list[float] = [float(x) for x in (prev.get("lags_s") or [])][-100:]
        self.protect_gaps_s: list[float] = [float(x) for x in (prev.get("protect_gaps_s") or [])][-100:]
        self.last_tick_ms = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_eval: dict[str, Any] = dict(prev.get("last_eval") or {})
        self._provider: Any = None
        self._d1: dict[str, tuple[int, Any]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="box-timer", daemon=True)
        self._thread.start()
        log.info("Box zamanlayıcısı başladı: yoklama %.0f sn, koruyucu tick en az %.0f sn arayla", self.poll_s, self.tick_every_s)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 — zamanlayıcı arızası ana turu ve sonraki yoklamayı DURDURMAZ
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("Box zamanlayıcısı döngü arızası: %s", self.last_error)
            self._stop.wait(self.poll_s)

    # ------------------------------------------------------------------ tek adım
    def run_once(self, now_ms: int | None = None) -> dict[str, Any]:
        now_ms = int(now_ms if now_ms is not None else self.clock_ms())
        bar_open = ((now_ms - self.settle_ms) // M5_MS) * M5_MS - M5_MS        # son KAPANMIŞ 5m mumunun açılışı
        if bar_open > self.last_bar_open:
            return self._evaluate(bar_open, now_ms)
        if self.book.ledger.positions and now_ms - self.last_tick_ms >= self.tick_every_s * 1000:
            return self._protect(now_ms)
        return {"idle": True, "last_bar_open": self.last_bar_open}

    def _prov(self) -> Any:
        if self._provider is None:
            self._provider = self.provider_factory()
        return self._provider

    def _frame(self, df: Any, tf: str, now_ms: int) -> Any:
        from .data import drop_unclosed_last_bar, prepare
        cols = [c for c in ("timestamp", "open", "high", "low", "close", "volume") if c in df.columns]
        return drop_unclosed_last_bar(prepare(df[cols]), tf, now_ms=now_ms)

    def _daily(self, prov: Any, sym: str, now_ms: int) -> Any:
        day0 = now_ms - now_ms % D1_MS
        hit = self._d1.get(sym)
        if hit is not None and hit[0] == day0:
            return hit[1]
        df = self._frame(prov.klines(sym, "1d", limit=D1_LIMIT, end_ms=now_ms), "1d", now_ms)
        self._d1[sym] = (day0, df)
        return df

    def _marks(self, prov: Any, syms: list[str], now_ms: int) -> tuple[dict[str, TickData], dict[str, float], dict[str, dict]]:
        """Doğrulanmış perp mark (`premiumIndex`, borsanın kendi zamanıyla) → `verified_price` hükmü. Koruyucu izleyiciyle
        TEK yol (`protective_monitor.perp_marks`)."""
        from .protective_monitor import perp_marks
        return perp_marks(prov, syms, now_ms)

    def _bars_1h(self, prov: Any, syms: list[str], marks_f: dict[str, float], now_ms: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym in syms:
            try:
                df = self._frame(prov.klines(sym, "1h", limit=48, end_ms=now_ms), "1h", now_ms)
            except Exception as exc:  # noqa: BLE001 — bar yoksa güncel fiyat izlemesi sürer
                log.warning("Box zamanlayıcısı %s 1h alınamadı: %s", sym, exc)
                continue
            rows = [{"timestamp": int(t), "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
                    for t, o, h, lo, c in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"])]
            out[sym] = {"tf": "1h", "rows": rows, "mark": float(marks_f.get(sym) or 0.0), "market": "USDM_PERP",
                        "source": "binance_usdm", "first_bar_ms": rows[0]["timestamp"] if rows else 0}
        return out

    def _traded_bars(self) -> set[tuple[str, int]]:
        """Defterin giriş kayıtlarındaki (sembol, sinyal mumu) çiftleri — aynı mumdan ikinci giriş engeli."""
        out: set[tuple[str, int]] = set()
        try:
            for r in self.book.memory.iter_rows():
                if not isinstance(r, dict) or r.get("kind") not in (None, "entry"):
                    continue
                ts = (r.get("features") or {}).get("signal_ts")
                if r.get("symbol") and ts is not None:
                    out.add((str(r["symbol"]), int(ts)))
        except Exception as exc:  # noqa: BLE001 — okunamazsa zamanlayıcı düzeyi tekilleştirme yine geçerli
            log.warning("Box zamanlayıcısı giriş kaydı okunamadı: %s", exc)
        return out

    def _evaluate(self, bar_open: int, now_ms: int) -> dict[str, Any]:
        t0 = time.time()
        now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        prov = self._prov()
        universe = list(dict.fromkeys(self.symbols_fn() or []))
        held = list(self.book.ledger.positions)
        syms = list(dict.fromkeys(universe + held))
        run_id = "boxtimer-%d" % bar_open
        frames: dict[str, dict] = {}
        provs: dict[str, dict] = {}
        fetch_errors = 0
        for sym in syms:
            try:
                d1 = self._daily(prov, sym, now_ms)
                m5 = self._frame(prov.klines(sym, "5m", limit=M5_LIMIT, end_ms=now_ms), "5m", now_ms)
            except Exception as exc:  # noqa: BLE001 — sembol düşerse diğerleri sürer; kural bu sembolde veri hükmüyle reddeder
                fetch_errors += 1
                log.warning("Box zamanlayıcısı %s mumları alınamadı: %s", sym, exc)
                continue
            frames[sym] = {"1d": d1, "5m": m5}
            bound = {}
            for tf, df in (("1d", d1), ("5m", m5)):
                if df is not None and len(df):
                    bound[tf] = {"last_ts": int(df["timestamp"].iloc[-1]), "n": int(len(df))}
            provs[sym] = {"market": "USDM_PERP", "source": "binance_usdm", "entry_ok": True, "tour_id": run_id,
                          "frames": bound, "as_of_ms": now_ms, "bound_at": iso(now)}
        pmarks, pmarks_f, gaps = self._marks(prov, syms, now_ms)
        traded = self._traded_bars()
        eligible = [s for s in universe if (s, bar_open) not in traded]
        blocked = len(universe) - len(eligible)
        pbars = self._bars_1h(prov, [s for s in held if s in pmarks_f], pmarks_f, now_ms) if held else {}
        with self.book.lock:
            self.book.run_id = run_id
            self.book.step(symbols=eligible, frames_by_symbol=frames, marks=pmarks, marks_f=pmarks_f, now=now,
                           provenance_by_symbol=provs, data_gaps=gaps)
            self.book.apply_closed_bars(pbars, now=now, funding_rate_lookup=self.funding_rates)
            self.book.tick(pmarks, now=now, funding_rate_lookup=self.funding_rates, bar_advance=False, source="box_timer",
                           apply_clock=self.clock_ms)
            self.book.save(pmarks_f, now)
        missed = max(0, (bar_open - self.last_bar_open) // M5_MS - 1) if self.last_bar_open else 0
        lag = round((now_ms - (bar_open + M5_MS)) / 1000.0, 1)
        if self.last_tick_ms:
            self.protect_gaps_s = (self.protect_gaps_s + [round((now_ms - self.last_tick_ms) / 1000.0, 1)])[-100:]
        self.last_tick_ms = now_ms
        self.last_bar_open = bar_open
        self.evaluations += 1
        self.missed_bars += int(missed)
        self.duplicates_blocked += int(blocked)
        self.lags_s = (self.lags_s + [lag])[-100:]
        self.last_eval = {"bar_open": iso(datetime.fromtimestamp(bar_open / 1000.0, tz=timezone.utc)), "at": iso(now),
                          "lag_s": lag, "symbols": len(syms), "eligible": len(eligible), "frames": len(frames),
                          "fetch_errors": fetch_errors, "price_gaps": len(gaps), "missed_before": int(missed),
                          "duplicates_blocked": int(blocked), "positions": sorted(self.book.ledger.positions),
                          "seconds": round(time.time() - t0, 2), "run_id": run_id}
        self._write_status(now)
        return dict(self.last_eval)

    def _protect(self, now_ms: int) -> dict[str, Any]:
        now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        prov = self._prov()
        expect = self.book.held_ids()                     # kimlik anlık görüntüsü (kısa kilit)
        held = list(expect)
        pmarks, pmarks_f, gaps = self._marks(prov, held, now_ms)      # AĞ — kilit DIŞINDA
        # tek kısa atomik bölüm (boşluk → korumalı tick → kayıt); koruyucu izleyici de aynı defteri aynı kilitle günceller
        self.book.protect(pmarks, pmarks_f, gaps, now=now, expect=expect, source="box_timer",
                          funding_rate_lookup=self.funding_rates, apply_clock=self.clock_ms)
        if self.last_tick_ms:
            self.protect_gaps_s = (self.protect_gaps_s + [round((now_ms - self.last_tick_ms) / 1000.0, 1)])[-100:]
        self.last_tick_ms = now_ms
        self._write_status(now)
        return {"protect": True, "positions": held, "priced": len(pmarks_f)}

    def status(self) -> dict[str, Any]:
        lags = sorted(self.lags_s)
        return {"schema_version": "box_timer_v1", "alive": self.alive, "last_bar_open": self.last_bar_open,
                "evaluations": self.evaluations, "missed_bars": self.missed_bars,
                "duplicates_blocked": self.duplicates_blocked, "errors": self.errors, "last_error": self.last_error,
                "lags_s": self.lags_s[-100:], "lag_p50_s": lags[len(lags) // 2] if lags else None,
                "lag_max_s": lags[-1] if lags else None, "protect_gaps_s": self.protect_gaps_s[-100:],
                "last_eval": self.last_eval, "poll_s": self.poll_s, "tick_every_s": self.tick_every_s}

    def _write_status(self, now: datetime) -> None:
        try:
            atomic_write_json(self.status_path, self.status() | {"generated_at": iso(now)})
        except Exception as exc:  # noqa: BLE001
            log.warning("Box zamanlayıcısı durumu yazılamadı: %s", exc)


__all__ = ["BoxTimer", "M5_MS", "STATUS_FILE"]
