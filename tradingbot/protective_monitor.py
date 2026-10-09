"""KORUYUCU ÇIKIŞ İZLEYİCİSİ (2026-09-24) — beş futures defterinin (ana bot, T2, M2/TSMOM28, Box, formasyon) açık
pozisyonlarını ANA TURDAN BAĞIMSIZ, kendi iş parçacığında ~60 sn'de bir güncel, doğrulanmış USDⓈ-M perp mark ile denetler.

Sorun (ölçüldü, 2026-09-23 VPS kopyası): `watch` döngüsü tek iş parçacıklıdır; 60 sn'lik çıkış kontrolü yalnız turlar
ARASINDAKİ beklemede koşar. Ağır tur (442 sn) boyunca T2/M2 ~370 sn, formasyon defteri 527 sn izlenmedi; bloke olan bir
turda (ağ zaman aşımı, uzun indeks işi) hiç izlenmez.

Sözleşme:
* AĞ, hiçbir defter kilidi tutulmadan yapılır. Her defter kendi KISA atomik bölümünde güncellenir: kilit → kimlik/sıra
  denetimi → tick → kaydet → kilit bırak. Funding'in ağ adımı (tur `_funding_step`, tarayıcı `funding_step`) bu yolda
  YOKTUR; tick gerçekleşmiş funding kaynağını yalnız bellekten okur (mevcut idempotent sözleşme aynen).
* FİYAT KİMLİĞİ: tek toplu `premiumIndex` isteği (USDⓈ-M perp mark + borsanın kendi zamanı) → `verified_price` (sonlu,
  pozitif, yaş ≤ PRICE_MAX_AGE_S, gelecekte değil). Spot ticker KULLANILMAZ. Geçerli fiyat yoksa o sembolde tick YOK;
  boşluk defterde gerekçesiyle görünür (uydurma fiyat/dolum yok).
* ÇİFT KAPANIŞ YOK: fiyat alınmadan ÖNCE kaydedilen pozisyon kimliği kilit altında yeniden doğrulanır (`expect`). Tur ile
  izleyici aynı defter kilidinde sıralanır; kapanan pozisyon sözlükten düşer, ikinci kez kapatılamaz. Arada tur pozisyonu
  kapatıp aynı sembolde yenisini açtıysa eski gözlem yeni pozisyona uygulanmaz. Miktar (TP1 kısmi) daima kilit altındaki
  GÜNCEL pozisyondan okunur.
* ZAMAN: fiyatın borsa (kaynak) zamanı ile alınma zamanı ayrı taşınır ve pozisyona uygulananlar `meta.mark_src_ts_ms` /
  `meta.mark_fetched_ts_ms`e yazılır (defterle kalıcı). Karşılaştırılabilir borsa zamanlarında daha ESKİ fiyat paysız
  reddedilir; tazelik UYGULAMA anında (kilit altında) yeniden denetlenir. Kapanmış bar uçları
  (`apply_closed_bars_to_ledger`) ayrı sözleşmedir ve bu denetimden geçmez.
* İzleyici iş parçacığı öğrenme/risk/bildirim YAPMAZ: ana defterin kapanışları kalıcı bir kuyruğa yazılır, öğreniciler
  ana iş parçacığında kalır (bkz. `engine_v3.drain_protective_closes`).
* ÖLÇÜM (`ObservationLog`): defter/pozisyon başına ardışık doğrulanmış gözlemler arası en uzun aralık ve hâlâ süren
  aralık `protective_monitor.json`a yazılır; hedefi (60 sn) aşan her aralık ayrıca listelenir. Açık pozisyonu olmayan
  defter "doğrulandı" SAYILMAZ (`open_positions: 0`).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

from .accounting import TickData
from .core import atomic_write_json, iso

log = logging.getLogger(__name__)

STATUS_FILE = "protective_monitor.json"
#: Kesinti sonrası ilk güncel fiyat gözlemleri (kesinti kayıtlarından ayrı dosya; salt ekleme)
FIRST_OBS_FILE = "monitoring_gap_observations.jsonl"
DEFAULT_INTERVAL_S = 60.0
#: Fiyatı defterin kullandığı tek kaynaktan: USDⓈ-M perp mark (premiumIndex), borsa zamanıyla.
PRICE_SOURCE = "binance_usdm.premiumIndex.markPrice"
#: Uygulama anında tazelik (2026-09-24): `strategy_paper.verified_price` ile AYNI eşikler (180 sn yaş, 120 sn ileri saat
#: payı) — fiyat alınırken taze olsa bile kilit beklemesi/uzun tur sonrasında UYGULANIRKEN bayatsa tick yapılmaz.
from .strategy_paper import PRICE_FUTURE_SKEW_S, PRICE_MAX_AGE_S  # noqa: E402


def _now_ms() -> int:
    return int(time.time() * 1000)


def _append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    import json
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        log.warning("kesinti gözlem kaydı yazılamadı: %s", exc)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def mark_ts_ms(td: Any) -> int | None:
    """TickData'nın bilinen en iyi fiyat zamanı (`ts`: kaynak varsa o, yoksa alınma) — ms ya da None."""
    from .strategy_paper import parse_ts_ms
    return parse_ts_ms(getattr(td, "ts", None) or None)


def tick_times(td: Any) -> tuple[int | None, int | None]:
    """(borsa KAYNAK zamanı, ALINMA zamanı) — ms ya da None. Eski biçim (yalnız `ts`, iki alan da boş) kaynak zamanı sayılır."""
    from .strategy_paper import parse_ts_ms
    src_s, got_s = getattr(td, "src_ts", "") or "", getattr(td, "fetched_ts", "") or ""
    if not src_s and not got_s:
        return parse_ts_ms(getattr(td, "ts", None) or None), None
    return parse_ts_ms(src_s or None), parse_ts_ms(got_s or None)


def live_tick(v: dict[str, Any]) -> TickData:
    """`verified_price` hükmünden fiyat tiki: kaynak (borsa) ve alınma zamanı AYRI alanlarda."""
    px = Decimal(str(float(v["mark"])))
    src, got = v.get("source_ts_ms"), v.get("fetched_at_ms")
    return TickData(last=px, mark=px, ts=iso(_dt(v["price_ts_ms"])) if v.get("price_ts_ms") else "",
                    src_ts=iso(_dt(src), timespec="milliseconds") if src else "",
                    fetched_ts=iso(_dt(got), timespec="milliseconds") if got else "")


# ---------------------------------------------------------------------- fiyat
def perp_marks(prov: Any, syms: Iterable[str], now_ms: int) -> tuple[dict[str, TickData], dict[str, float], dict[str, dict]]:
    """Doğrulanmış USDⓈ-M perp mark: tek toplu `premiumIndex` (borsanın `time` alanı fiyat zamanıdır), düşerse sembol başına
    `mark_price`. Hüküm `strategy_paper.verified_price` (Box zamanlayıcısı ile AYNI yol). Döner: (tick, float, boşluk)."""
    from .market.providers import to_raw
    from .strategy_paper import verified_price
    syms = list(dict.fromkeys(syms or []))
    rows: dict[str, dict] = {}
    if syms:
        try:
            data = prov.http.get(f"{prov.prefix}/premiumIndex", weight=10)
            rows = {str(r.get("symbol")): r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)}
        except Exception as exc:  # noqa: BLE001 — toplu istek düşerse sembol başına dene
            log.warning("koruyucu izleyici: premiumIndex toplu alınamadı: %s", exc)
    out, outf, gaps = {}, {}, {}
    for sym in syms:
        r = rows.get(to_raw(sym))
        if r is None:
            try:
                m = prov.mark_price(sym) or {}
                r = {"markPrice": m.get("mark"), "time": m.get("ts")}
            except Exception as exc:  # noqa: BLE001
                r = {"error": str(exc)[:120]}
        snap = {"ts": now_ms / 1000.0, "errors": [r["error"]] if r.get("error") else [],
                "funding": {"mark": r.get("markPrice"), "ts": r.get("time")}}
        v = verified_price(snap, now_ms=now_ms)
        if not v["ok"]:
            gaps[sym] = {"reason": v["reason"], "detail": v["detail"], "age_s": v["age_s"], "at": iso(_dt(now_ms))}
            continue
        out[sym] = live_tick(v)
        outf[sym] = float(v["mark"])
    return out, outf, gaps


def perp_price_fn(provider_factory: Callable[[], Any]) -> Callable[[list[str], int], tuple]:
    """İzleyicinin fiyat fonksiyonu: sağlayıcı tembel ve YALNIZ bu iş parçacığına ait (tur nesneleri paylaşılmaz)."""
    holder: dict[str, Any] = {}

    def fn(syms: list[str], now_ms: int):
        if "p" not in holder:
            holder["p"] = provider_factory()
        return perp_marks(holder["p"], syms, now_ms)
    return fn


# ---------------------------------------------------------------------- tick (kimlik + sıra korumalı)
def guarded_tick(ledger: Any, marks: dict[str, Any] | None, *, now: datetime, funding_rate_lookup: Any = None,
                 bar_advance: bool = False, expect: dict[str, str] | None = None,
                 apply_clock: Callable[[], int] | None = None) -> tuple[list, dict[str, Any]]:
    """Defter tick'i, koruma denetimleriyle (çağıran defter kilidini TUTAR; ağ YOK):

    * KİMLİK — `expect` {sembol: pozisyon kimliği}: fiyat alınmadan önceki kimlik; eşleşmeyen/eksik pozisyon tick'lenmez
      (`POSITION_CHANGED` / `NOT_IN_SNAPSHOT`). None → defterdeki bütün pozisyonlar.
    * TAZELİK UYGULAMA ANINDA — `apply_clock()` KİLİT ALTINDA okunur (canlı yollar kendi gerçek saatini verir: izleyici,
      Box zamanlayıcısı, tarayıcı, motor; verilmezse çağıranın `now`u); fiyat zamanı (kaynak, yoksa
      alınma) `PRICE_MAX_AGE_S`den eski → `STALE_AT_APPLY`, `PRICE_FUTURE_SKEW_S`den ileride → `PRICE_TIME_IN_FUTURE`.
      Alınırken taze olan fiyat kilit beklemesi ya da uzun tur sonrasında bayatlamışsa uygulanmaz.
    * SIRA — kaynak (borsa) zamanı ile alınma zamanı AYRI tutulur (`meta.mark_src_ts_ms` / `meta.mark_fetched_ts_ms`,
      defterle kalıcı). İki fiyatın da borsa zamanı varsa daha ESKİ borsa zamanlı fiyat PAYSIZ reddedilir; biri yoksa
      alınma zamanları karşılaştırılır (önbellekten gelen eski alınma reddedilir). `OLDER_THAN_APPLIED`.
    * Hiç zamanı olmayan tik (yalnız test/elle yollar; canlı fiyat yolları daima zamanlıdır) denetlenemez: uygulanır ama
      sırayı ilerletmez.

    Döner: (kapanan kayıtlar, {"applied": {sym: {id, price, price_ts_ms, source_ts_ms, fetched_ts_ms, applied_ms}},
    "skipped": {sym: neden}})."""
    use: dict[str, TickData] = {}
    applied: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    apply_ms = int(apply_clock() if apply_clock is not None else now.timestamp() * 1000)
    for sym, raw in (marks or {}).items():
        pos = ledger.positions.get(sym)
        if pos is None:
            continue
        if expect is not None:
            if sym not in expect:
                skipped[sym] = "NOT_IN_SNAPSHOT"
                continue
            if str(expect[sym]) != str(pos.id):
                skipped[sym] = "POSITION_CHANGED"
                continue
        td = TickData.coerce(raw)
        src, got = tick_times(td)
        ref = src if src is not None else got
        if ref is not None:
            if ref > apply_ms + int(PRICE_FUTURE_SKEW_S * 1000):
                skipped[sym] = "PRICE_TIME_IN_FUTURE"
                continue
            if apply_ms - ref > int(PRICE_MAX_AGE_S * 1000):
                skipped[sym] = "STALE_AT_APPLY"
                continue
        meta = pos.meta if isinstance(getattr(pos, "meta", None), dict) else {}
        last_src, last_got = meta.get("mark_src_ts_ms"), meta.get("mark_fetched_ts_ms")
        if src is not None and last_src is not None:
            older = int(src) < int(last_src)
        elif got is not None and last_got is not None:
            older = int(got) < int(last_got)
        else:
            older = False
        if older:
            skipped[sym] = "OLDER_THAN_APPLIED"
            continue
        use[sym] = td
        applied[sym] = {"id": str(pos.id), "price": float(td.ref), "price_ts_ms": ref, "source_ts_ms": src, "fetched_ts_ms": got,
                        "applied_ms": apply_ms}
    recs = ledger.tick(use, now_utc=now, funding_rate_lookup=funding_rate_lookup, bar_advance=bar_advance) if use else []
    for sym, info in applied.items():
        pos = ledger.positions.get(sym)
        if pos is None or not isinstance(getattr(pos, "meta", None), dict):
            continue
        for key, val in (("mark_src_ts_ms", info["source_ts_ms"]), ("mark_fetched_ts_ms", info["fetched_ts_ms"])):
            if val is not None:
                pos.meta[key] = max(int(pos.meta.get(key) or 0), int(val))
    return recs, {"applied": applied, "skipped": skipped, "apply_ms": apply_ms}


def note_gap_first_observations(gap: dict[str, Any] | None, *, book_key: str, state_path: Path | str,
                                applied: dict[str, dict[str, Any]], now: datetime, closed_ids: Iterable[str] = ()) -> None:
    """İZLEME KESİNTİSİNDEN SONRA İLK GÖZLEM: yükleme anındaki her pozisyon için ilk geçerli güncel fiyatın GERÇEK gözlem
    zamanı (ve fiyatın kaynak zamanı) kesinti kaydına (bellek + `FIRST_OBS_FILE`, kesinti dosyasından AYRI: orada kesinti
    başına tek satır kalır) eklenir — kesinti tahmini işlemle değil, gözlemle kapanır."""
    if not gap or gap.get("first_observation_done"):
        return
    held = set(gap.get("positions") or [])
    seen = gap.setdefault("first_observations", {})
    closed = set(closed_ids or ())
    for sym, info in (applied or {}).items():
        if sym not in held or sym in seen:
            continue
        ts = info.get("price_ts_ms")
        seen_ms = info.get("applied_ms")                # fiyatın GERÇEKTEN uygulandığı an (tur `now`u fiyat alınmadan önceki andır)
        row = {"position_id": info.get("id"), "price": info.get("price"), "observed_at": iso(_dt(seen_ms)) if seen_ms else iso(now),
               "price_ts": iso(_dt(ts)) if ts else None, "closed_by_this_observation": info.get("id") in closed}
        seen[sym] = row
        _append_jsonl(Path(state_path) / FIRST_OBS_FILE, {"kind": "MONITORING_GAP_FIRST_OBSERVATION", "book": book_key, "symbol": sym,
                                                          "gap_from": gap.get("from"), "gap_to": gap.get("to"), **row,
                                                          "price_source": "usdm_perp_mark", "recorded_at": iso(now)})
    if held and held <= set(seen):
        gap["first_observation_done"] = True


# ---------------------------------------------------------------------- ölçüm
class ObservationLog:
    """Defter/pozisyon başına doğrulanmış koruyucu gözlem aralıkları (iş parçacığı güvenli, bellek içi).

    İlk gözlemde aralık, pozisyonun açılışından (ya da ölçümün başladığı andan — hangisi SONRAYSA) sayılır; böylece
    izleme başlamadan önce açık kalmış süre de görünür ve hiç gözlenmeyen pozisyon `current_gap_s` ile yakalanır.

    Gözlem zamanı = UYGULANAN fiyatın kendi zamanı (`price_ts_ms`: borsa, yoksa alınma), çağıranın `now`u DEĞİL: tur
    `now`unu fiyatları almadan önce alır ve kilit/ağ beklemesinden sonra uygular; o anı yazmak son gözlemi GERİYE taşır
    ve sonraki aralığı şişirirdi. Son gözlem yalnız İLERİ gider (daha eski fiyatlı gözlem aralığı kısaltmaz).
    Ölçüm sırasında açılan pozisyonda taban, izleyicinin onu İÇERMEYEN son anlık görüntüsüdür (`snapshot`): tur girişleri
    `opened_at`'ı turun karar anıyla yazar (defterde henüz yokken), o an taban olsaydı var olmayan bir bekleme sayılırdı."""

    def __init__(self, target_s: float = DEFAULT_INTERVAL_S, clock_ms: Callable[[], int] = _now_ms) -> None:
        self.target_s = float(target_s)
        self.clock_ms = clock_ms
        self.started_ms = int(clock_ms())
        self._lock = threading.Lock()
        self._pos: dict[str, dict[str, dict[str, Any]]] = {}
        self._snaps: dict[str, dict[str, Any]] = {}
        self.exceeded: list[dict[str, Any]] = []

    def snapshot(self, book_key: str, ids: Iterable[str], ms: int) -> None:
        """İzleyicinin geçiş başındaki kimlik anlık görüntüsü: yeni beliren kimlik için "en geç şu anda yoktu" sınırı."""
        ids = {str(x) for x in ids}
        with self._lock:
            st = self._snaps.setdefault(str(book_key), {"ms": None, "ids": set(), "appeared": {}})
            known = self._pos.get(str(book_key)) or {}
            if st["ms"] is not None:
                for pid in ids - st["ids"]:
                    if pid not in known:
                        st["appeared"].setdefault(pid, st["ms"])
            st["ms"], st["ids"] = int(ms), ids

    def appeared(self, book_key: str, ids: Iterable[str], ms: int) -> None:
        """Turda açılan pozisyon: `ms` anında (adımdan hemen önce) defterde YOKTU — anlık görüntüden daha kesin taban."""
        with self._lock:
            st = self._snaps.setdefault(str(book_key), {"ms": None, "ids": set(), "appeared": {}})
            known = self._pos.get(str(book_key)) or {}
            for pid in (str(x) for x in ids):
                if pid not in known:
                    st["appeared"][pid] = max(int(st["appeared"].get(pid) or 0), int(ms))

    def _floor_ms(self, book_key: str, pid: str) -> int:
        st = self._snaps.get(book_key)
        if not st or st["ms"] is None:
            return self.started_ms
        if pid in st["appeared"]:
            return max(self.started_ms, int(st["appeared"].pop(pid)))
        if pid not in st["ids"]:                        # son anlık görüntüden SONRA açıldı
            return max(self.started_ms, int(st["ms"]))
        return self.started_ms

    def note(self, book_key: str, applied: dict[str, dict[str, Any]], now_ms: int, *, opened_ms: dict[str, int] | None = None,
             source: str = "") -> None:
        with self._lock:
            book = self._pos.setdefault(str(book_key), {})
            for sym, info in (applied or {}).items():
                pid = str(info.get("id"))
                obs_ms = int(info.get("price_ts_ms") or now_ms)
                rec = book.get(pid)
                if rec is None:
                    base = max(int((opened_ms or {}).get(sym) or 0), self._floor_ms(str(book_key), pid))
                    rec = book[pid] = {"symbol": sym, "first_obs_ms": obs_ms, "last_obs_ms": None, "base_ms": base,
                                       "n": 0, "max_gap_s": 0.0, "sources": {}}
                prev = rec["last_obs_ms"] if rec["last_obs_ms"] is not None else rec["base_ms"]
                gap = max(0.0, (obs_ms - int(prev)) / 1000.0)
                if gap > rec["max_gap_s"]:
                    rec["max_gap_s"] = round(gap, 1)
                if gap > self.target_s:
                    self.exceeded = (self.exceeded + [{"book": book_key, "symbol": sym, "position_id": pid, "gap_s": round(gap, 1),
                                                       "until": iso(_dt(obs_ms)), "source": source}])[-200:]
                rec["last_obs_ms"] = max(obs_ms, int(rec["last_obs_ms"] or 0))
                rec["n"] += 1
                rec["sources"][source or "?"] = rec["sources"].get(source or "?", 0) + 1

    def prune(self, book_key: str, open_ids: Iterable[str]) -> None:
        keep = {str(x) for x in open_ids}
        with self._lock:
            book = self._pos.get(str(book_key)) or {}
            for pid in [p for p in book if p not in keep]:
                book[pid]["closed"] = True
            st = self._snaps.get(str(book_key))
            for pid in [p for p in (st or {}).get("appeared", {}) if p not in keep]:
                st["appeared"].pop(pid, None)

    def status(self, now_ms: int | None = None, open_ids: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
        now_ms = int(now_ms if now_ms is not None else self.clock_ms())
        out: dict[str, Any] = {}
        worst = 0.0
        with self._lock:
            keys = set(self._pos) | set(open_ids or {})
            for key in sorted(keys):
                book = self._pos.get(key) or {}
                open_now = (open_ids or {}).get(key)
                rows = {}
                for pid, rec in book.items():
                    is_open = (pid in set((open_now or {}).values())) if open_now is not None else not rec.get("closed")
                    ref = rec["last_obs_ms"] if rec["last_obs_ms"] is not None else rec["base_ms"]
                    cur = round(max(0.0, (now_ms - int(ref)) / 1000.0), 1) if is_open else None
                    rows[pid] = {"symbol": rec["symbol"], "open": is_open, "observations": rec["n"],
                                 "max_gap_s": rec["max_gap_s"], "current_gap_s": cur, "sources": dict(rec["sources"]),
                                 "last_obs": iso(_dt(rec["last_obs_ms"])) if rec["last_obs_ms"] else None}
                    worst = max(worst, rec["max_gap_s"], cur or 0.0)
                # hiç gözlenmemiş açık pozisyon: ölçüm başından beri izlenmedi
                for sym, pid in (open_now or {}).items():
                    if pid not in rows:
                        cur = round(max(0.0, (now_ms - self.started_ms) / 1000.0), 1)
                        rows[pid] = {"symbol": sym, "open": True, "observations": 0, "max_gap_s": None, "current_gap_s": cur,
                                     "sources": {}, "last_obs": None}
                        worst = max(worst, cur)
                n_open = sum(1 for r in rows.values() if r["open"])
                mx = [r["max_gap_s"] for r in rows.values() if r["max_gap_s"] is not None]
                cu = [r["current_gap_s"] for r in rows.values() if r["current_gap_s"] is not None]
                out[key] = {"open_positions": n_open, "measured": bool(rows),
                            "max_gap_s": max(mx + cu) if (mx or cu) else None, "positions": rows}
            exceeded = list(self.exceeded)
        return {"target_s": self.target_s, "since": iso(_dt(self.started_ms)), "worst_gap_s": round(worst, 1),
                "over_target": worst > self.target_s, "exceeded": exceeded[-50:], "books": out}


# ---------------------------------------------------------------------- defter tutamaçları
class BookHandle:
    """Kâğıt defter (T2/M2/Box `StrategyBook`, formasyon `PatternBook`) için izleyici tutamacı."""

    def __init__(self, book: Any) -> None:
        self.book = book
        self.key = str(getattr(book, "key", "book"))

    def held(self) -> dict[str, str]:
        return self.book.held_ids()

    def protect(self, marks, marks_f, gaps, now, expect, apply_clock=None) -> list:
        return self.book.protect(marks, marks_f, gaps, now=now, expect=expect, source="monitor", apply_clock=apply_clock)


class ProtectiveMonitor:
    """Beş defterin koruyucu izleyicisi — kendi iş parçacığı, turdan bağımsız, ~`interval_s` aralıkla.

    `handles_fn()` → tutamaçlar (`key`, `held()`, `protect(...)`); `price_fn(semboller, now_ms)` → (tick, float, boşluk).
    Saat (`clock_ms`) ve bekleme (`wait`) enjekte edilebilir: testler uyku süresine değil olaylara dayanır."""

    def __init__(self, *, handles_fn: Callable[[], list], price_fn: Callable[[list[str], int], tuple], state_path: Path | str,
                 interval_s: float = DEFAULT_INTERVAL_S, clock_ms: Callable[[], int] = _now_ms,
                 observer: ObservationLog | None = None, waiter: Callable[[float], Any] | None = None) -> None:
        self.handles_fn = handles_fn
        self.price_fn = price_fn
        self.state_path = Path(state_path)
        self.status_path = self.state_path / STATUS_FILE
        self.interval_s = max(1.0, float(interval_s))
        self.clock_ms = clock_ms
        self.observer = observer or ObservationLog(target_s=self.interval_s, clock_ms=clock_ms)
        self.runs = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_run: dict[str, Any] = {}
        self.durations_s: list[float] = []
        self.pass_gaps_s: list[float] = []
        self._last_start_ms: int | None = None
        self._stop = threading.Event()
        #: `poke` ile erken uyanma (yeni pozisyon bir sonraki düzenli geçişi beklemez); `stop` da uyandırır
        self._wake = threading.Event()
        self.pokes = 0
        self.last_poke: str | None = None
        #: geçişler arası bekleme; testler bunu olayla sürer (uyku süresine dayanmaz)
        self._waiter = waiter or self._wake.wait
        self._thread: threading.Thread | None = None
        self._open_ids: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="protective-monitor", daemon=True)
        self._thread.start()
        log.info("koruyucu izleyici başladı: her %.0f sn, beş defter, fiyat %s", self.interval_s, PRICE_SOURCE)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def poke(self, reason: str = "") -> bool:
        """Hemen bir geçiş iste (iş parçacığı canlıysa; beklemez, ağ yok). Tur yeni pozisyon açtığında çağrılır: tur giriş
        fiyatını adımın başında alır, pozisyon deftere sonra girer; ilk TAZE fiyatlı koruyucu kontrol bir sonraki düzenli
        geçişi (≤ `interval_s`) beklemesin. Geçiş sırasında gelen dürtme kaybolmaz: bekleme hemen döner."""
        if not self.alive:
            return False
        self.pokes += 1
        self.last_poke = str(reason)[:120]
        self._wake.set()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            t0 = self.clock_ms()
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 — izleyici arızası iş parçacığını ÖLDÜRMEZ
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.exception("koruyucu izleyici döngü arızası: %s", exc)
            spent = max(0.0, (self.clock_ms() - t0) / 1000.0)
            self._waiter(max(0.5, self.interval_s - spent))

    # ------------------------------------------------------------------ tek geçiş
    def run_once(self, now_ms: int | None = None) -> dict[str, Any]:
        start_ms = int(now_ms if now_ms is not None else self.clock_ms())
        if self._last_start_ms is not None:
            self.pass_gaps_s = (self.pass_gaps_s + [round((start_ms - self._last_start_ms) / 1000.0, 1)])[-200:]
        self._last_start_ms = start_ms
        handles = list(self.handles_fn() or [])
        held: dict[str, dict[str, str]] = {}
        errs: dict[str, str] = {}
        for h in handles:                               # kısa kilit: yalnız kimlik anlık görüntüsü
            try:
                held[h.key] = dict(h.held() or {})
                self.observer.snapshot(h.key, held[h.key].values(), start_ms)
            except Exception as exc:  # noqa: BLE001
                errs[h.key] = f"held: {type(exc).__name__}: {exc}"[:200]
        syms = list(dict.fromkeys(s for ids in held.values() for s in ids))
        closed: dict[str, list[str]] = {}
        gaps: dict[str, dict] = {}
        if syms:
            marks, marks_f, gaps = self.price_fn(syms, start_ms)          # AĞ — hiçbir defter kilidi tutulmuyor
            obs_ms = int(self.clock_ms()) if now_ms is None else start_ms
            now = _dt(obs_ms)
            for h in handles:
                ids = held.get(h.key) or {}
                if not ids:
                    continue
                try:
                    recs = h.protect({s: marks[s] for s in ids if s in marks}, {s: marks_f[s] for s in ids if s in marks_f},
                                     {s: gaps[s] for s in ids if s in gaps}, now, ids, apply_clock=self.clock_ms)
                    closed[h.key] = [str(getattr(r, "id", "")) for r in (recs or [])]
                except Exception as exc:  # noqa: BLE001 — bir defterin arızası diğerlerini DURDURMAZ
                    errs[h.key] = f"protect: {type(exc).__name__}: {exc}"[:200]
                    log.warning("koruyucu izleyici %s defteri başarısız: %s", h.key, exc)
        # güncel açık kimlikler (ölçüm için; kilit kısa)
        open_ids: dict[str, dict[str, str]] = {}
        for h in handles:
            try:
                open_ids[h.key] = dict(h.held() or {})
                self.observer.prune(h.key, open_ids[h.key].values())
            except Exception:  # noqa: BLE001
                open_ids[h.key] = held.get(h.key) or {}
        self._open_ids = open_ids
        dur = round((self.clock_ms() - start_ms) / 1000.0, 3) if now_ms is None else 0.0
        self.durations_s = (self.durations_s + [dur])[-200:]
        self.runs += 1
        if errs:
            self.errors += len(errs)
            self.last_error = "; ".join(f"{k}: {v}" for k, v in errs.items())[:300]
        self.last_run = {"at": iso(_dt(start_ms)), "symbols": len(syms), "price_gaps": sorted(gaps),
                         "closed": {k: v for k, v in closed.items() if v}, "errors": errs, "seconds": dur,
                         "books": {k: sorted(v) for k, v in held.items()}}
        self._write_status(start_ms)
        return dict(self.last_run)

    def status(self, now_ms: int | None = None) -> dict[str, Any]:
        d = sorted(self.durations_s)
        return {"schema_version": "protective_monitor_v1", "alive": self.alive, "interval_s": self.interval_s,
                "price_source": PRICE_SOURCE, "runs": self.runs, "pokes": self.pokes, "errors": self.errors, "last_error": self.last_error,
                "last_run": self.last_run, "duration_p50_s": d[len(d) // 2] if d else None,
                "duration_max_s": d[-1] if d else None, "pass_gap_max_s": max(self.pass_gaps_s) if self.pass_gaps_s else None,
                "observations": self.observer.status(now_ms, self._open_ids)}

    def _write_status(self, now_ms: int) -> None:
        try:
            atomic_write_json(self.status_path, self.status(now_ms) | {"generated_at": iso(_dt(now_ms))})
        except Exception as exc:  # noqa: BLE001
            log.warning("koruyucu izleyici durumu yazılamadı: %s", exc)


__all__ = ["BookHandle", "FIRST_OBS_FILE", "ObservationLog", "PRICE_SOURCE", "ProtectiveMonitor", "STATUS_FILE", "guarded_tick",
           "live_tick", "mark_ts_ms", "note_gap_first_observations", "perp_marks", "perp_price_fn", "tick_times"]
