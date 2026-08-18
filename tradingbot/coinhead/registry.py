"""CoinHeadRegistry — sembol başına tek aktif Coin Head; kilit, eşzamanlılık sınırı, bayat sonuç koruması, kalıcı state."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, utc_now
from .head import CoinHead, CoinHeadConfig, CoinHeadInputs
from .schema import CoinHeadDecision

log = logging.getLogger(__name__)


class CoinHeadRegistry:
    def __init__(self, cfg: CoinHeadConfig | None = None, max_workers: int = 4):
        self.cfg = cfg or CoinHeadConfig()
        self.max_workers = max_workers
        self._heads: dict[str, CoinHead] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._seq: dict[str, str] = {}          # sembol → son işlenen snapshot_id (monoton)
        self._guard = threading.Lock()
        self.last_decisions: dict[str, CoinHeadDecision] = {}
        self.chief: dict[str, Any] | None = None

    def get_or_create(self, symbol: str) -> CoinHead:
        with self._guard:
            if symbol not in self._heads:
                self._heads[symbol] = CoinHead(symbol, self.cfg)
                self._locks[symbol] = threading.Lock()
            return self._heads[symbol]

    def run(self, symbol: str, inputs: CoinHeadInputs) -> CoinHeadDecision | None:
        """Aynı sembol için çakışan analiz engellenir; daha eski snapshot yeni sonucun üzerine yazamaz."""
        head = self.get_or_create(symbol)
        lock = self._locks[symbol]
        if not lock.acquire(blocking=False):
            log.warning("%s için analiz zaten sürüyor — atlandı", symbol)
            return None
        try:
            snap = inputs.snapshot_id or ""
            prev = self._seq.get(symbol, "")
            if snap and prev and snap < prev:
                log.warning("%s: bayat snapshot %s < %s — sonuç yazılmadı", symbol, snap, prev)
                return None
            dec = head.decide(inputs)
            with self._guard:
                if snap:
                    self._seq[symbol] = snap
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

    def to_state_dict(self, run_id: str = "") -> dict:
        return {"generated_at": iso(utc_now()), "run_id": run_id,
                "heads": [d.to_dict(include_reports=True) for d in self.last_decisions.values()], "chief": self.chief}

    def save(self, state_dir: Path | str, run_id: str = "") -> Path:
        p = Path(state_dir) / "coin_heads.json"
        atomic_write_json(p, self.to_state_dict(run_id))
        return p
