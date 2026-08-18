"""CoinHeadRegistry — sembol başına tek aktif Coin Head; kilit, eşzamanlılık sınırı, bayat sonuç koruması, kalıcı state.

Bayatlık kuralı: snapshot'lar OLAY ZAMANINA göre sıralanır — `(snapshot_at_ms, snapshot_seq)`; `snapshot_id` yalnızca opak
kimliktir (aynı id tekrar gelirse idempotent reddedilir), asla sözlük sırasıyla karşılaştırılmaz.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now
from .head import CoinHead, CoinHeadConfig, CoinHeadInputs
from .schema import CoinHeadDecision

log = logging.getLogger(__name__)

_LEGACY_AT_MS = -1   # eski state yalnız hash snapshot_id içeriyorsa: zamanı bilinmiyor → her gerçek zaman damgası daha yeni sayılır


class CoinHeadRegistry:
    def __init__(self, cfg: CoinHeadConfig | None = None, max_workers: int = 4):
        self.cfg = cfg or CoinHeadConfig()
        self.max_workers = max_workers
        self._heads: dict[str, CoinHead] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._seq: dict[str, tuple[int, int, str]] = {}   # sembol → (snapshot_at_ms, snapshot_seq, snapshot_id) — son kabul edilen
        self._guard = threading.Lock()
        self.last_decisions: dict[str, CoinHeadDecision] = {}
        self.chief: dict[str, Any] | None = None
        self.drops: dict[str, int] = {"stale_snapshot": 0, "duplicate_snapshot": 0}

    def get_or_create(self, symbol: str) -> CoinHead:
        with self._guard:
            if symbol not in self._heads:
                self._heads[symbol] = CoinHead(symbol, self.cfg)
                self._locks[symbol] = threading.Lock()
            return self._heads[symbol]

    @staticmethod
    def order_key(inputs: CoinHeadInputs) -> tuple[int, int]:
        at = inputs.snapshot_at_ms if inputs.snapshot_at_ms is not None else inputs.now_ms
        return (int(at) if at is not None else _LEGACY_AT_MS, int(inputs.snapshot_seq or 0))

    def run(self, symbol: str, inputs: CoinHeadInputs) -> CoinHeadDecision | None:
        """Aynı sembol için çakışan analiz engellenir; daha eski (olay zamanı) ya da tekrar eden snapshot yeni sonucun üzerine yazamaz."""
        head = self.get_or_create(symbol)
        lock = self._locks[symbol]
        if not lock.acquire(blocking=False):
            log.warning("%s için analiz zaten sürüyor — atlandı", symbol)
            return None
        try:
            snap = inputs.snapshot_id or ""
            key = self.order_key(inputs)
            prev = self._seq.get(symbol)
            if snap and prev is not None:
                if snap == prev[2]:
                    self.drops["duplicate_snapshot"] += 1
                    log.warning("%s: aynı snapshot %s tekrar geldi — yoksayıldı (idempotent)", symbol, snap)
                    return None
                if key <= (prev[0], prev[1]):
                    self.drops["stale_snapshot"] += 1
                    log.warning("%s: bayat snapshot %s @%s <= %s @%s — sonuç yazılmadı (STALE)", symbol, snap, key, prev[2], prev[:2])
                    return None
            dec = head.decide(inputs)
            with self._guard:
                if snap:
                    self._seq[symbol] = (key[0], key[1], snap)
                self.last_decisions[symbol] = dec
            return dec
        finally:
            lock.release()

    def run_many(self, inputs_by_symbol: dict[str, CoinHeadInputs]) -> dict[str, CoinHeadDecision]:
        out: dict[str, CoinHeadDecision] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as ex:
            futs = {ex.submit(self.run, s, i): s for s, i in inputs_by_symbol.items()}
            for f in as_completed(futs):
                s = futs[f]
                try:
                    r = f.result()
                except Exception as exc:  # noqa: BLE001 — bir coin'in hatası diğerlerini durdurmaz; kayıt altına alınır
                    log.exception("%s coin head hatası: %s", s, exc)
                    r = None
                if r is not None:
                    out[s] = r
        return out

    # ------------------------------------------------------------------ kalıcı state
    def snapshot_order(self) -> dict[str, dict]:
        with self._guard:
            return {s: {"at_ms": v[0], "seq": v[1], "snapshot_id": v[2]} for s, v in sorted(self._seq.items())}

    def to_state_dict(self, run_id: str = "") -> dict:
        return {"generated_at": iso(utc_now()), "run_id": run_id,
                "heads": [d.to_dict(include_reports=True) for d in self.last_decisions.values()], "chief": self.chief,
                "snapshot_order": self.snapshot_order()}

    def save(self, state_dir: Path | str, run_id: str = "") -> Path:
        p = Path(state_dir) / "coin_heads.json"
        atomic_write_json(p, self.to_state_dict(run_id))
        return p

    def load(self, state_dir: Path | str) -> int:
        """Kalıcı sıralama durumunu yükle. Yeni format: `snapshot_order`. Legacy (yalnız hash `snapshot_id`): zaman bilinmez →
        yalnız aynı id tekrarı reddedilir, ilk zaman damgalı snapshot kabul edilir. Bozuk/eksik dosya → boş (fail-safe)."""
        p = Path(state_dir) / "coin_heads.json"
        try:
            data = read_json(p, default=None)
        except Exception as exc:  # noqa: BLE001 — bozuk state kararları engellemez; ilk snapshot kabul edilir
            log.warning("coin_heads.json okunamadı: %s", exc)
            data = None
        if not isinstance(data, dict):
            return 0
        loaded: dict[str, tuple[int, int, str]] = {}
        order = data.get("snapshot_order")
        if isinstance(order, dict):
            for s, v in order.items():
                try:
                    loaded[str(s)] = (int(v["at_ms"]), int(v.get("seq", 0)), str(v.get("snapshot_id", "")))
                except (KeyError, TypeError, ValueError):
                    continue
        else:   # legacy migration: heads[].snapshot_id → zamanı bilinmeyen opak id
            for h in data.get("heads") or []:
                if isinstance(h, dict) and h.get("symbol") and h.get("snapshot_id"):
                    loaded[str(h["symbol"])] = (_LEGACY_AT_MS, 0, str(h["snapshot_id"]))
        with self._guard:
            self._seq.update(loaded)
        return len(loaded)
