"""Değişmez işlem hafızası (LAYER 1) — yalnız ekleme; asla kesilmez. SQLite varsa `learning_features`/`trade_outcomes`
tablolarına da yazar (duck typing: `repo.upsert(table, row)`), yoksa/ek olarak `state/trade_memory.jsonl`."""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator

from ..core import iso, new_id, payload_hash, utc_now


def _append_line(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


SOURCES = ("LIVE_PAPER", "HISTORICAL_REPLAY", "SHADOW", "TESTNET", "LIVE", "SYNTHETIC_TEST",
           "STRATEGY_PAPER",   # V10: tek kurallı stratejinin AYRI kâğıt defteri (ana hafızayla karışmaz)
           "PATTERN_PAPER")    # 2026-09-16: formasyon PAPER trader'ının AYRI defteri (T2/M2 ve ana hafızayla karışmaz)


class TradeMemory:
    """Değişmez işlem hafızası (JSONL). Her kayıtta zorunlu `source` namespace: LIVE_PAPER | HISTORICAL_REPLAY | SHADOW | TESTNET | LIVE.
    Replay kayıtları gerçek PAPER hafızasıyla KARIŞMAZ: farklı `source` (ve replay için ayrı dosya yolu)."""

    def __init__(self, path: Path | str | None = None, repo: Any | None = None, *, source: str = "LIVE_PAPER"):
        self.path = Path(path) if path else None
        self.repo = repo
        if source not in SOURCES:
            raise ValueError(f"bilinmeyen memory source: {source}")
        self.source = source
        #: İsteğe bağlı artımlı okuyucu (`MemoryTail`); motor bağlar, `reconcile` çıkış kimliklerini buradan okur.
        self.tail: "MemoryTail | None" = None

    # ------------------------------------------------------------ yazım
    def record_entry(self, snapshot: dict[str, Any]) -> str:
        """Giriş anı: bütün ajan raporları, coin head kararı, dissent, veto, risk kararı, plan, model/prompt sürümleri, veri tazeliği."""
        tid = str(snapshot.get("trade_id") or snapshot.get("id") or new_id("trade"))
        src = str(snapshot.get("source") or self.source)
        if src not in SOURCES:
            raise ValueError(f"bilinmeyen memory source: {src}")
        row = {"kind": "entry", "trade_id": tid, "recorded_at": iso(utc_now()), "hash": payload_hash(snapshot), "source": src, **{k: v for k, v in snapshot.items() if k != "source"}}
        if self.path:
            _append_line(self.path, row)
        if self.repo is not None:
            try:
                self.repo.upsert("learning_features", {"id": f"lf_{tid}", "position_id": tid, "symbol": snapshot.get("symbol"),
                                                       "ts_utc": row["recorded_at"], "feature_set_version": str(snapshot.get("feature_version", 2)),
                                                       "label": None, "snapshot": snapshot}, ignore_existing=True)
            except Exception:  # noqa: BLE001 — DB opsiyonel; JSONL birincil kayıt
                pass
        return tid

    def record_exit(self, trade_id: str, outcome: dict[str, Any], price_path: list[dict] | None = None, postmortem: dict | None = None) -> None:
        row = {"kind": "exit", "trade_id": trade_id, "recorded_at": iso(utc_now()), "source": self.source, "outcome": outcome,
               "price_path": price_path or [], "postmortem": postmortem or {}}
        if self.path:
            _append_line(self.path, row)
        if self.repo is not None:
            try:
                self.repo.upsert("trade_outcomes", {"id": f"to_{trade_id}", "position_id": trade_id, "symbol": outcome.get("symbol"),
                                                    "market_type": outcome.get("market_type"), "side": outcome.get("side"),
                                                    "exit_reason": outcome.get("exit_reason"), "closed_at_utc": outcome.get("closed_at"),
                                                    "pnl": outcome.get("net_pnl", outcome.get("pnl")), "r_multiple": outcome.get("r_multiple"),
                                                    "won": 1 if float(outcome.get("r_multiple", 0) or 0) > 0 else 0, "outcome": outcome,
                                                    "postmortem": postmortem or {}}, ignore_existing=True)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ okuma
    def iter_rows(self, *, source: str | None = "auto") -> Iterator[dict]:
        """source="auto" → yalnız bu hafızanın namespace'i (eski kayıtlar source'suz → LIVE_PAPER sayılır); None → hepsi."""
        want = self.source if source == "auto" else source
        for row in self._iter_all_rows():
            if want is None or str(row.get("source") or "LIVE_PAPER") == want:
                yield row

    def _iter_all_rows(self) -> Iterator[dict]:
        if not self.path or not self.path.exists():
            return iter(())
        def gen():
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return gen()

    def get(self, trade_id: str) -> dict | None:
        out: dict | None = None
        for r in self.iter_rows():
            if r.get("trade_id") != trade_id:
                continue
            if r.get("kind") == "entry":
                out = dict(r)
            elif r.get("kind") == "exit" and out is not None:
                out["outcome"], out["postmortem"], out["price_path"] = r.get("outcome"), r.get("postmortem"), r.get("price_path")
        return out

    def trades(self, *, closed_only: bool = False, limit: int | None = None,
               project: Callable[[dict], dict] | None = None) -> list[dict]:
        """Giriş + çıkış birleşimi. `project` (2026-09-28, öğrenme modu; üçüncü doğrulama turu): satır okunurken SINIRLI
        kopyaya indirilir (bütün tam satırlar aynı anda bellekte durmaz). Sözleşme: `project`, `outcome`/`postmortem`
        eklemeyle yer değiştirir — `project(project(e) ∪ X) == project(e ∪ X)` — sonuç `[project(r) for r in trades()]`
        ile BİREBİR aynıdır. None → eski yol aynen."""
        merged: dict[str, dict] = {}
        for r in self.iter_rows():
            tid = r.get("trade_id")
            if r.get("kind") == "entry":
                merged[tid] = dict(r) if project is None else project(dict(r))
            elif r.get("kind") == "exit" and tid in merged:
                merged[tid]["outcome"], merged[tid]["postmortem"] = r.get("outcome"), r.get("postmortem")
                if project is not None:
                    merged[tid] = project(merged[tid])
        rows = [v for v in merged.values() if (v.get("outcome") if closed_only else True)]
        rows.sort(key=lambda x: x.get("recorded_at", ""))
        return rows[-limit:] if limit else rows

    def query(self, *, symbol: str | None = None, direction: str | None = None, setup: str | None = None, regime: str | None = None,
              since: str | None = None, limit: int = 50) -> list[dict]:
        out = []
        for t in self.trades(closed_only=True):
            if symbol and t.get("symbol") != symbol:
                continue
            if direction and t.get("direction") != direction:
                continue
            if setup and t.get("setup_type") != setup:
                continue
            if regime and t.get("regime") != regime:
                continue
            if since and t.get("recorded_at", "") < since:
                continue
            out.append(t)
        return out[-limit:]

    def count(self) -> int:
        return len(self.trades())


class MemoryTail:
    """`TradeMemory` JSONL'nin ARTIMLI okuyucusu (2026-09-28, öğrenme modu; üçüncü doğrulama turu).

    Sorun: hafıza yalnız eklenir ve öğrenmede ana bot günde ~45 giriş (~73 KB/satır: ajan + şef raporları) yazar. Motor her
    turda dosyayı BAŞTAN birkaç kez ayrıştırıyordu (deneyim havuzu her değişimde; giriş değerlendirmesi, replay denetimi ve
    kapanış zinciri her turda) — 157 MB'lık dosyada okuma başına ~4–5 sn CPU ve +520–590 MB tepe RSS; haftalar içinde
    worker MemoryMax'a yürürdü.

    Okuyucu yalnız YENİ tam satırları ayrıştırır (bayt ofseti + dosya kimliği (st_dev, st_ino) + son 4 KB bekçi). Dosya
    değiştirildi/kesildi/döndürüldü (kimlik, boy ya da bekçi uyuşmuyor) → baştan TAM yeniden okuma (yine satır satır). Çıktılar
    eski okuyucularla BİREBİR aynıdır:

    * `closed_rows()`  == `[project(r) for r in memory.trades(closed_only=True)]` (sıralama dahil);
    * `last_entries(k)` == `[r for r in memory.iter_rows() if isinstance(r, dict) and r.get("kind") == "entry"][-k:]`
      (satırlar diskten her çağrıda TAZE ayrıştırılır; bellekte yalnız son `keep_entries` girişin ofseti durur);
    * `exit_ids()`     == çıkış satırlarının `trade_id` kümesi (`reconcile._memory_exit_ids`).

    Eski okuyucunun farklı davranabileceği her durumda (JSON olup sözlük olmayan satır, UTF-8 hatası, satır içi `\\r`,
    sonu satır sonu olmayan dolu son satır) okuyucu "temiz değil" der ve çağıran ESKİ yolu çalıştırır (bit-aynı).
    Kimlik değişimi dışında (yeniden okuma) bu durum yapışkandır — yalnız eklenen dosyada bir kez görülen satır kalır."""

    GUARD_BYTES = 4096

    def __init__(self, memory: TradeMemory, *, project: Callable[[dict], dict] | None = None, keep_entries: int = 0):
        self.memory = memory
        self.project = project
        self.keep_entries = max(0, int(keep_entries))
        self._lock = threading.Lock()
        self.full_loads = 0
        self.incremental_loads = 0
        self._reset(None)

    # ------------------------------------------------------------ durum
    def _reset(self, ident: tuple[int, int] | None) -> None:
        self._ident = ident
        self._offset = 0
        self._guard = b""
        self._merged: dict[Any, dict] = {}
        self._entries: deque[tuple[int, int]] = deque(maxlen=self.keep_entries or None)
        self._exit_ids: set[str] = set()
        self._anomaly = False
        self._partial = False

    def stats(self) -> dict[str, Any]:
        return {"offset": self._offset, "rows_open": len(self._merged), "entries_kept": len(self._entries),
                "exit_ids": len(self._exit_ids), "full_loads": self.full_loads,
                "incremental_loads": self.incremental_loads, "clean": not (self._anomaly or self._partial)}

    def _guard_ok(self, fh) -> bool:
        n = len(self._guard)
        if n == 0:
            return self._offset == 0
        fh.seek(self._offset - n)
        return fh.read(n) == self._guard

    def _sync(self, fh) -> bool:
        """`fh` (ikili, açık) ile durumu dosyanın SONUNA getirir. Döner: durum dosyanın tamamını temsil ediyor mu."""
        st = os.fstat(fh.fileno())
        ident = (int(st.st_dev), int(st.st_ino))
        if ident != self._ident or st.st_size < self._offset or not self._guard_ok(fh):
            self._reset(ident)
            self.full_loads += 1
        elif st.st_size > self._offset:
            self.incremental_loads += 1
        self._partial = False
        if st.st_size <= self._offset:
            return not self._anomaly
        fh.seek(self._offset)
        pos = self._offset
        last_nl = pos
        for raw in fh:
            n = len(raw)
            if not raw.endswith(b"\n"):
                # tamamlanmamış son satır: işlenmez, ofset ilerlemez; doluysa bu çağrı eski yola düşer
                if raw.strip():
                    self._partial = True
                break
            self._line(raw, pos, n)
            pos += n
            last_nl = pos
        if last_nl > self._offset:
            self._offset = last_nl
            k = min(self.GUARD_BYTES, last_nl)
            fh.seek(last_nl - k)
            self._guard = fh.read(k)
        return not (self._anomaly or self._partial)

    def _line(self, raw: bytes, pos: int, n: int) -> None:
        if self._anomaly:
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._anomaly = True
            return
        body = text[:-2] if text.endswith("\r\n") else text[:-1]
        if "\r" in body:                              # metin kipinde yalnız \r de satır böler → eski yol
            self._anomaly = True
            return
        line = text.strip()
        if not line:
            return
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(row, dict):
            self._anomaly = True                        # eski okuyucu `row.get` ile düşer → eski yol
            return
        if str(row.get("source") or "LIVE_PAPER") != self.memory.source:
            return
        kind, tid = row.get("kind"), row.get("trade_id")
        if kind == "entry":
            if self.project is not None:
                self._merged[tid] = self.project(dict(row))
            if self.keep_entries:
                self._entries.append((pos, n))
        elif kind == "exit":
            m = self._merged.get(tid) if self.project is not None else None
            if m is not None:
                m = dict(m)
                m["outcome"], m["postmortem"] = row.get("outcome"), row.get("postmortem")
                self._merged[tid] = self.project(m)
            if tid:
                self._exit_ids.add(str(tid))

    def _open(self):
        p = self.memory.path
        if not p:
            return None
        try:
            return open(p, "rb")
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------ okuyucular
    def closed_rows(self) -> list[dict]:
        """`[project(r) for r in memory.trades(closed_only=True)]` ile aynı (satır nesneleri okuyucu içinde paylaşılır;
        tüketici DEĞİŞTİRMEMELİ — deneyim havuzu yalnız okur)."""
        if self.project is None:
            raise ValueError("MemoryTail.closed_rows: project verilmedi")
        with self._lock:
            fh = self._open()
            if fh is None:
                self._reset(None)
                return []
            with fh:
                clean = self._sync(fh)
            if clean:
                rows = [v for v in self._merged.values() if v.get("outcome")]
                rows.sort(key=lambda x: x.get("recorded_at", ""))
                return rows
        return [self.project(r) for r in self.memory.trades(closed_only=True)]

    def last_entries(self, k: int) -> list[dict]:
        """Son `k` giriş satırı (bu hafızanın kaynağı), her çağrıda diskten TAZE ayrıştırılmış; `k <= keep_entries`."""
        k = int(k)
        if not 0 < k <= self.keep_entries:
            raise ValueError("MemoryTail.last_entries: 0 < k <= keep_entries olmalı")
        with self._lock:
            fh = self._open()
            if fh is None:
                self._reset(None)
                return []
            with fh:
                if self._sync(fh):
                    out = []
                    for pos, n in list(self._entries)[-k:]:
                        fh.seek(pos)
                        out.append(json.loads(fh.read(n).decode("utf-8").strip()))
                    return out
        return [r for r in self.memory.iter_rows() if isinstance(r, dict) and r.get("kind") == "entry"][-k:]

    def exit_ids(self) -> set[str] | None:
        """Çıkış satırlarının `trade_id` kümesi (kopya). Temiz değilse None → çağıran eski akışlı okumayı yapar."""
        with self._lock:
            fh = self._open()
            if fh is None:
                self._reset(None)
                return set()
            with fh:
                if self._sync(fh):
                    return set(self._exit_ids)
        return None
