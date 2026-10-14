# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — anlık görüntü çekirdeği için LRU önbellek (2026-09-29).

Aynı barları gören her kitap (gerçek ya da karşı-olgusal) AYNI çekirdek nesnesini alır: kitaplar arası "aynı durum"
birleşimi budur (SPEC_V1 §4.5). Yalnız ana iş parçacığı kullanır (kilit yok). Yalnız TAM pencereler önbelleğe girer.
G/Ç yok; `situation` dışında içe aktarım yok.

Anahtar (2026-09-29): SPEC_V1'deki `(sembol, h4.last_open, h1.last_open, btc.last_open, h4.n, h1.n, btc.n)` demetine
pencerelerin `input_sha`'sı EKLENMİŞTİR. Aynı son bar ve aynı n'ye sahip ama içeriği farklı iki pencere (ör. ortasında
eksik bar olan bir kaynak) spesifikasyondaki anahtarla çakışır ve anlık görüntünün `input_sha`'sı çekirdeğiyle
uyuşmazdı. Ek alan yalnız AYRIŞTIRIR, hiçbir zaman spesifikasyonun ayırdığını birleştirmez.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .situation import Window, compute_core, input_sha, is_btc_symbol


class SituationCache:
    """OrderedDict LRU: `get` isabet ettiğinde girdiyi en yeniye taşır; `put` taşarsa en eskiyi atar (2026-09-29).
    Dönen çekirdek sözlüğü PAYLAŞILIR — çağıran DEĞİŞTİRMEZ (`situation.snapshot` kendi kopyasını alır)."""

    def __init__(self, max_entries: int = 2048) -> None:
        m = int(max_entries)
        if m < 1:
            raise ValueError("SituationCache: max_entries >= 1 olmalı (gelen %r)" % (max_entries,))
        self.max_entries = m
        self._d: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.puts = 0
        self.evictions = 0
        self.refused = 0

    @staticmethod
    def key_for(symbol: str, h4: Window, h1: Window, btc: Window, *, sha: str | None = None) -> tuple | None:
        """Önbellek anahtarı; pencerelerden biri TAM değilse `None` (önbelleğe girmez)."""
        if not (h4.complete and h1.complete and btc.complete):
            return None
        return (str(symbol), h4.last_open_ms, h1.last_open_ms, btc.last_open_ms, h4.n, h1.n, btc.n,
                sha if sha is not None else input_sha(h4, h1, btc))

    def get(self, key: tuple | None) -> dict[str, Any] | None:
        if key is None:
            return None
        core = self._d.get(key)
        if core is None:
            self.misses += 1
            return None
        self._d.move_to_end(key)
        self.hits += 1
        return core

    def put(self, key: tuple | None, core: dict[str, Any]) -> None:
        if key is None:                                      # (2026-09-29) eksik pencere → önbelleğe ALINMAZ
            self.refused += 1
            return
        self._d[key] = core
        self._d.move_to_end(key)
        self.puts += 1
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)
            self.evictions += 1

    def get_or_compute(self, symbol: str, h4: Window, h1: Window, btc: Window, *, sha: str | None = None) -> dict[str, Any] | None:
        """Tam pencereler için çekirdek (önbellekten ya da `compute_core` ile hesaplanıp konarak); eksik pencerede
        `None` — o durumda `situation.snapshot` çekirdeği kendisi (boş pencereyle) hesaplar (2026-09-29)."""
        key = self.key_for(symbol, h4, h1, btc, sha=sha)
        if key is None:
            return None
        core = self.get(key)
        if core is None:
            core = compute_core(h4, h1, btc, is_btc=is_btc_symbol(symbol))
            self.put(key, core)
        return core

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._d), "max_entries": self.max_entries, "hits": self.hits, "misses": self.misses,
                "puts": self.puts, "evictions": self.evictions, "refused": self.refused}

    def __len__(self) -> int:
        return len(self._d)


__all__ = ["SituationCache"]
