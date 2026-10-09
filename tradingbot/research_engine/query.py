"""P2b — `engine-query` (§8, §10 P2): `trade <id>`, `why-lost [--book B] [--days N]`, `day <YYYY-MM-DD>`.

SALT-OKUNUR: kilit almaz, hiçbir şey yazmaz, ledger okumaz; yalnız `data/research` altındaki günlük (`journal/tj_v1`),
atıf (`attribution/`) ve günlük hedef satırlarını okur. Çıktı Türkçe, şablondan (LLM yok), ≤ 150 satır ve ≤ 4 KB
(`report.bound`). Ağ yok. Giriş noktası `cli_v3.cmd_engine_query` (tembel import).
"""
from __future__ import annotations

import re
from datetime import datetime

from .ledgers import utc_now
from .paths import EnginePaths

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def run_query(paths: EnginePaths, *, topic: str, arg: str | None = None, book: str | None = None, days: int = 7,
              now: datetime | None = None) -> tuple[list[str], int]:
    """(satırlar, çıkış kodu). 0 = cevap; 2 = kullanım hatası."""
    from . import report as RP
    now = now or utc_now()
    if topic == "trade":
        if not arg:
            return ["kullanım: engine-query trade <trade_key | işlem kimliği>"], 2
        return RP.query_trade(paths, arg), 0
    if topic == "why-lost":
        if days < 1 or days > 400:
            return ["--days 1..400 olmalı"], 2
        return RP.query_why_lost(paths, now=now, book=book, days=days), 0
    if topic == "day":
        if not arg or not DAY_RE.match(arg):
            return ["kullanım: engine-query day <YYYY-MM-DD>"], 2
        return RP.query_day(paths, arg), 0
    return [f"bilinmeyen konu: {topic} (trade | why-lost | day)"], 2


__all__ = ["run_query"]
