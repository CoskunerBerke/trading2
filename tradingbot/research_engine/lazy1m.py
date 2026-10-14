"""P2 — tembel 1m doldurma planı (§3.3 "1m (yol için) … son 400 gün, **tembel** (yalnız gerektiğinde, P2)").

Gece birimi AĞSIZDIR: yolu 1m'den kurulamayan işlemlerin UTC günlerini `paths/needs_1m.json`'a yazar
(`pathrec.plan_needs`). Veri birimi (`datastore.DataRun._lazy_1m_pass`, ağlı TEK birim) bu dosyayı ertesi gece okur ve
YALNIZ bu günlerin data.binance.vision **gün zip'lerini** P1b kurallarıyla çeker: arşiv-önce, `.CHECKSUM` doğrulaması
(yoksa `archive_unverified`), 4h yayın pencereleri ve son tarih kapısı (`gate`), disk sınırı, doğrulanmış 1m zip'inin
silinmesi, seri başına yeniden deneme/hata yalıtımı. **REST kullanılmaz** (henüz arşivde olmayan gün yol için 5m'ye
düşer; ertesi gece gün zip'i gelir). Tembel seriler PLANLI değildir (`planned: false`): tazelik ölçütüne (V2) girmez,
bayat sayılmaz.

Bu modül ağ kullanmaz; yalnız gün listesini doğrular ve sınırlar (saf).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

UTC = timezone.utc
DAY_MS = 86_400_000
NEEDS_SCHEMA = "engine_lazy_1m_needs_v1"
#: veri birimi çalıştırma başına en çok gün zip'i (her biri + `.CHECKSUM` = 2 istek; CDN ağırlık harcamaz)
LAZY_MAX_DAYS_PER_RUN = 400
LAZY_HISTORY_DAYS = 400
ROLE_LAZY_1M = "lazy_1m"


def _day_ms(day: str) -> int | None:
    try:
        y, m, d = (int(x) for x in str(day)[:10].split("-"))
        return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def read_needs(paths: Any) -> dict[str, Any] | None:
    """`paths/needs_1m.json` (gece biriminin yazdığı ihtiyaç listesi); yoksa/bozuksa/şeması farklıysa None."""
    try:
        with open(paths.needs_1m, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == NEEDS_SCHEMA else None


def lazy_plan(needs: dict[str, Any] | None, *, now_ms: int, plan_keys: Iterable[str] = (), delisted: Iterable[str] = (),
              max_days: int = LAZY_MAX_DAYS_PER_RUN) -> list[tuple[str, str, list[tuple[str, int, int]]]]:
    """İhtiyaç belgesinden (market, sembol, [(gün damgası, başlangıç ms, bitiş ms)]) listesi — deterministik. Atlananlar:
    1m dışı seriler, zaten PLANLI 1m seriler (altın vadeliler P1b'de tam doldurulur), delist semboller, tamamlanmamış
    (bugünün) günü, `LAZY_HISTORY_DAYS`'ten eski günler. Sınır aşılırsa en YENİ günler önce."""
    if not isinstance(needs, dict) or needs.get("schema") != NEEDS_SCHEMA:
        return []
    today = int(now_ms) // DAY_MS * DAY_MS
    floor = today - LAZY_HISTORY_DAYS * DAY_MS
    planned, dl = set(plan_keys), set(delisted)
    flat: list[tuple[int, str, str, str]] = []
    for key, days in sorted((needs.get("series") or {}).items()):
        parts = str(key).split("/")
        if len(parts) != 3 or parts[2] != "1m" or parts[0] not in ("futures", "spot"):
            continue
        if key in planned or f"{parts[0]}/{parts[1]}" in dl:
            continue
        for d in days if isinstance(days, list) else []:
            a = _day_ms(d)
            if a is None or a < floor or a + DAY_MS > today:
                continue
            flat.append((a, parts[0], parts[1], str(d)[:10]))
    flat = sorted(set(flat), key=lambda x: (-x[0], x[1], x[2]))[:max(0, int(max_days))]
    by: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
    for a, mk, sym, d in flat:
        by.setdefault((mk, sym), []).append((d, a, a + DAY_MS))
    return [(mk, sym, sorted(v, key=lambda x: x[1])) for (mk, sym), v in sorted(by.items())]


__all__ = ["LAZY_HISTORY_DAYS", "LAZY_MAX_DAYS_PER_RUN", "NEEDS_SCHEMA", "ROLE_LAZY_1M", "lazy_plan", "read_needs"]
