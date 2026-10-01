"""Sınırlı tam-geçmiş toplam hafızası — hiçbir arşiv sonucu "erişilemez veri" olamaz.

Boşluk: exemplar retrieval `max_scan` penceresiyle sınırlıdır (bilinçli — aday başına arşiv
taranmaz). Pencerenin DIŞINDA kalan eski ama benzer bir arşiv sonucu, hiçbir kanala katkı
veremiyordu. Gerçek (REAL_PAPER) kapanışların tam geçmişi `HierarchicalRate` prior'ında zaten
yaşar; bu modül aynı garantiyi ARŞİVLENMİŞ gölge sonuçlar için verir.

Sahiplik sözleşmesi (çift sayım yasak):

* Hiyerarşik prior  → gerçek kapanışların TAM geçmişi (residual payı mevcut mekanizma).
* Exemplar kanalı   → `query_pool` penceresindeki en yeni kayıtlar (kesin `as_of`).
* Aggregate kanalı  → arşivlenmiş gölge KALANI: sayılan exemplar'lar hücreden DÜŞÜLÜR.

No-lookahead: istatistikler AY kovalarında tutulur; yalnız `as_of`'tan önce TAMAMEN bitmiş
aylar sayılır. Yeni pencere zaten exemplar kanalındadır → boşluk oluşmaz, sızıntı imkânsız.
Etiket zamanı bilinmeyen kayıt hiçbir kovaya giremez (fail-closed).

Kardinalite SINIRLI: anahtar seviyeleri (aşağıda) + ay kovası. Ham float anahtar YOK; profil
anahtarı `feature_profile` (≤5 kova/boyut) ile sınırlıdır. `max_cells` aşılırsa yeni L1/L2
hücresi açılmaz, kayıt üst seviyeye katlanır (`folded_cells` sayacı) — veri kaybolmaz,
yalnız çözünürlük düşer.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

AGGREGATE_SCHEMA_VERSION = "aggregate_memory_v1"

#: Fallback sırası — bölüm 9 hiyerarşisi. En spesifik seviyeden genele.
LEVEL_KEYS = ("L1", "L2", "L3", "L4", "L5")

_STAT_FIELDS = ("n", "w", "wr", "wr2", "wins", "losses", "real_n", "shadow_n",
                "first_ms", "last_ms")


def _month_key(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_end_ms(month: str) -> int:
    """Ay kovasının bitişi (bir sonraki ayın ilk milisaniyesi)."""
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        y, m = y + 1, 1
    else:
        m += 1
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _s(x: Any) -> str:
    v = str(x) if x not in (None, "") else "-"
    return v.replace("|", "_")[:48]


def level_keys_for(*, symbol: Any, direction: Any, regime: Any, setup: Any,
                   profile: Any) -> dict[str, str]:
    """Bir deneyimin/sorgunun 5 seviye anahtarı — bölüm 9 fallback hiyerarşisi."""
    sym, dirn, reg, st, prof = _s(symbol), _s(direction), _s(regime), _s(setup), _s(profile)
    return {"L1": f"1|{sym}|{dirn}|{reg}|{st}|{prof}",
            "L2": f"2|{sym}|{dirn}|{st}",
            "L3": f"3|{sym}",
            "L4": f"4|{reg}|{st}",
            "L5": "5|GLOBAL"}


def _keys_for_experience(e: Any) -> dict[str, str]:
    return level_keys_for(symbol=getattr(e, "symbol", None),
                          direction=getattr(e, "direction", None),
                          regime=getattr(e, "regime", None),
                          setup=getattr(e, "setup", None),
                          profile=getattr(e, "feature_profile", None))


def _new_stats() -> dict[str, float]:
    return {"n": 0, "w": 0.0, "wr": 0.0, "wr2": 0.0, "wins": 0, "losses": 0,
            "real_n": 0, "shadow_n": 0, "first_ms": 0, "last_ms": 0}


class AggregateBook:
    """Seviye × ay kovalı, sınırlı kardinaliteli toplam istatistik defteri.

    Deterministiktir: aynı deneyim akışı → aynı sözlük (rebuild kanıtı buna dayanır).
    """

    def __init__(self, *, max_cells: int = 50_000):
        self.max_cells = int(max_cells)
        #: cells[level_key][month] = stats
        self.cells: dict[str, dict[str, dict[str, float]]] = {}
        self.total_added = 0
        self.skipped_no_label = 0
        self.folded_cells = 0

    # ------------------------------------------------------------------ güncelleme
    def _cell(self, key: str, month: str, *, allow_new: bool) -> dict[str, float] | None:
        lvl = self.cells.get(key)
        if lvl is None:
            if not allow_new and len(self.cells) >= self.max_cells:
                return None
            lvl = self.cells.setdefault(key, {})
        st = lvl.get(month)
        if st is None:
            st = lvl[month] = _new_stats()
        return st

    def add(self, e: Any) -> bool:
        """Tek deneyimi BÜTÜN seviyelerine ekler. Etiket zamanı yoksa eklemez (fail-closed)."""
        lts = getattr(e, "label_ts_ms", None)
        if not isinstance(lts, (int, float)) or not math.isfinite(float(lts)):
            self.skipped_no_label += 1
            return False
        r = getattr(e, "r_multiple", None)
        if not isinstance(r, (int, float)) or not math.isfinite(float(r)):
            self.skipped_no_label += 1
            return False
        w = float(getattr(e, "weight", 1.0) or 0.0)
        month = _month_key(int(lts))
        is_real = str(getattr(e, "source", "")) == "REAL_PAPER"
        keys = _keys_for_experience(e)
        for lk in LEVEL_KEYS:
            key = keys[lk]
            # Kardinalite tavanı yalnız SPESİFİK seviyelerde yeni hücre açmayı durdurur;
            # L3/L4/L5 daima kabul eder → kayıt asla tamamen dışarıda kalmaz.
            allow_new = lk in ("L3", "L4", "L5") or len(self.cells) < self.max_cells
            st = self._cell(key, month, allow_new=allow_new)
            if st is None:
                self.folded_cells += 1
                continue
            st["n"] += 1
            st["w"] += w
            st["wr"] += w * float(r)
            st["wr2"] += w * float(r) * float(r)
            st["wins"] += 1 if float(r) > 0 else 0
            st["losses"] += 1 if float(r) < 0 else 0
            st["real_n"] += 1 if is_real else 0
            st["shadow_n"] += 0 if is_real else 1
            st["first_ms"] = int(lts) if not st["first_ms"] else min(int(st["first_ms"]), int(lts))
            st["last_ms"] = max(int(st["last_ms"]), int(lts))
        self.total_added += 1
        return True

    def add_many(self, exps: Iterable[Any]) -> int:
        return sum(1 for e in exps if self.add(e))

    # ------------------------------------------------------------------ sorgu
    def query(self, *, symbol: Any, direction: Any, regime: Any, setup: Any, profile: Any,
              as_of_ms: int | None, subtract: Iterable[Any] = (),
              min_n: int = 1) -> dict[str, Any] | None:
        """En spesifik dolu seviyeden SINIRLI toplam kanıt. Bulunamazsa None.

        * Yalnız `as_of`'tan önce TAMAMEN bitmiş ay kovaları sayılır (no-lookahead).
        * `subtract`: exemplar kanalında ZATEN sayılmış deneyimler — aynı hücre+aydaysa
          katkıları düşülür (çift sayım yasak). Negatife inmez (clamp).
        """
        keys = level_keys_for(symbol=symbol, direction=direction, regime=regime,
                              setup=setup, profile=profile)
        # Sayılan exemplar'ların hücre/ay katkıları (yalnız etiketli olanlar)
        sub_by_cell: dict[tuple[str, str], dict[str, float]] = {}
        for e in subtract or ():
            lts = getattr(e, "label_ts_ms", None)
            r = getattr(e, "r_multiple", None)
            if not isinstance(lts, (int, float)) or not isinstance(r, (int, float)):
                continue
            m = _month_key(int(lts))
            w = float(getattr(e, "weight", 1.0) or 0.0)
            ekeys = _keys_for_experience(e)
            for lk in LEVEL_KEYS:
                cell = sub_by_cell.setdefault((ekeys[lk], m), {"n": 0, "w": 0.0, "wr": 0.0,
                                                               "wr2": 0.0, "wins": 0,
                                                               "losses": 0})
                cell["n"] += 1
                cell["w"] += w
                cell["wr"] += w * float(r)
                cell["wr2"] += w * float(r) * float(r)
                cell["wins"] += 1 if float(r) > 0 else 0
                cell["losses"] += 1 if float(r) < 0 else 0

        for lk in LEVEL_KEYS:
            key = keys[lk]
            months = self.cells.get(key)
            if not months:
                continue
            agg = _new_stats()
            used_months: list[str] = []
            subtracted = 0
            for month in sorted(months):
                if as_of_ms is not None and _month_end_ms(month) > int(as_of_ms):
                    continue                      # bitmemiş/gelecek ay ASLA sayılmaz
                st = months[month]
                sub = sub_by_cell.get((key, month))
                n = st["n"] - (sub["n"] if sub else 0)
                w = st["w"] - (sub["w"] if sub else 0.0)
                if sub:
                    subtracted += int(sub["n"])
                if n <= 0 or w <= 1e-12:
                    continue                      # clamp: negatif hücre yok
                agg["n"] += n
                agg["w"] += w
                agg["wr"] += st["wr"] - (sub["wr"] if sub else 0.0)
                agg["wr2"] += st["wr2"] - (sub["wr2"] if sub else 0.0)
                agg["wins"] += max(0, int(st["wins"] - (sub["wins"] if sub else 0)))
                agg["losses"] += max(0, int(st["losses"] - (sub["losses"] if sub else 0)))
                agg["real_n"] += int(st["real_n"])
                agg["shadow_n"] += int(st["shadow_n"])
                agg["first_ms"] = (int(st["first_ms"]) if not agg["first_ms"]
                                   else min(int(agg["first_ms"]), int(st["first_ms"])))
                agg["last_ms"] = max(int(agg["last_ms"]), int(st["last_ms"]))
                used_months.append(month)
            if agg["n"] >= max(1, int(min_n)) and agg["w"] > 0:
                mean_r = agg["wr"] / agg["w"]
                var = max(0.0, agg["wr2"] / agg["w"] - mean_r * mean_r)
                return {"schema_version": AGGREGATE_SCHEMA_VERSION,
                        "level": lk, "level_key": key,
                        "n": int(agg["n"]), "w_total": round(agg["w"], 6),
                        "mean_r": round(mean_r, 6), "std_r": round(math.sqrt(var), 6),
                        "wins": int(agg["wins"]), "losses": int(agg["losses"]),
                        "real_n": int(agg["real_n"]), "shadow_n": int(agg["shadow_n"]),
                        "first_ms": int(agg["first_ms"]) or None,
                        "last_ms": int(agg["last_ms"]) or None,
                        "months": len(used_months),
                        "subtracted_exemplars": subtracted,
                        "as_of_ms": as_of_ms}
        return None

    # ------------------------------------------------------------------ seri hale getirme
    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": AGGREGATE_SCHEMA_VERSION, "max_cells": self.max_cells,
                "total_added": self.total_added, "skipped_no_label": self.skipped_no_label,
                "folded_cells": self.folded_cells, "cells": self.cells}

    @classmethod
    def from_dict(cls, doc: dict[str, Any] | None) -> "AggregateBook":
        book = cls(max_cells=int((doc or {}).get("max_cells") or 50_000))
        if not isinstance(doc, dict) or not isinstance(doc.get("cells"), dict):
            return book
        cells: dict[str, dict[str, dict[str, float]]] = {}
        for key, months in doc["cells"].items():
            if not isinstance(months, dict):
                continue
            out_m: dict[str, dict[str, float]] = {}
            for month, st in months.items():
                if isinstance(st, dict) and all(k in st for k in ("n", "w", "wr")):
                    out_m[str(month)] = {k: st.get(k, 0) for k in _STAT_FIELDS}
            if out_m:
                cells[str(key)] = out_m
        book.cells = cells
        book.total_added = int(doc.get("total_added") or 0)
        book.skipped_no_label = int(doc.get("skipped_no_label") or 0)
        book.folded_cells = int(doc.get("folded_cells") or 0)
        return book

    def stats(self) -> dict[str, Any]:
        n_cells = len(self.cells)
        n_buckets = sum(len(m) for m in self.cells.values())
        first = last = None
        g = self.cells.get("5|GLOBAL") or {}
        for st in g.values():
            if st.get("first_ms"):
                first = int(st["first_ms"]) if first is None else min(first, int(st["first_ms"]))
            if st.get("last_ms"):
                last = max(last or 0, int(st["last_ms"]))
        total = sum(int(st.get("n") or 0) for st in g.values())
        return {"schema_version": AGGREGATE_SCHEMA_VERSION, "cells": n_cells,
                "month_buckets": n_buckets, "outcomes": total,
                "total_added": self.total_added, "skipped_no_label": self.skipped_no_label,
                "folded_cells": self.folded_cells,
                "oldest_ms": first, "newest_ms": last}


__all__ = ["AGGREGATE_SCHEMA_VERSION", "AggregateBook", "LEVEL_KEYS", "level_keys_for"]
