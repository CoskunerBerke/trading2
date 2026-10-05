# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a testleri için yardımcı (sahte VPS veri kökü + gerçek muhasebe sınıflarıyla sentetik
ledger'lar). Test dosyası DEĞİLDİR (pytest toplamaz); testler `sys.path` ile içe aktarır.

Ledger'lar worker'ın kendi sınıflarıyla (`FuturesLedgerV2`, `SpotLedger`) üretilir, böylece arşiv/uzlaştırma gerçek
dosya biçimine karşı sınanır. `save()` ledger'ın `updated_at`'ini verilen ana sabitler (`led.save()` duvar saatini
yazardı; mark yaşı ve PERP_PROXY seçimi deterministik olsun diye).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, SpotLedger
from tradingbot.research_engine import closes as C
from tradingbot.research_engine import daily_target as T
from tradingbot.research_engine.ledgers import iso
from tradingbot.research_engine.paths import EnginePaths

UTC = timezone.utc
NIGHT = timedelta(hours=1, minutes=40)


def at(day: str, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


def night_of(day: str) -> datetime:
    """D gününün gece çalıştırması (01:40 UTC) — S(D) bu anda alınır."""
    return at(day) + NIGHT


def trade(led: FuturesLedgerV2, sym: str, opened: datetime, entry: float, exit_px: float, *, lev: int = 2,
          notional: str = "100", side: str = "LONG", hold: timedelta = timedelta(minutes=30)):
    stop = entry * 0.95 if side == "LONG" else entry * 1.05
    pos = led.open(sym, side, D(str(entry)), SizeSpec(D(notional), AmountType.NOTIONAL, lev), stop=D(str(round(stop, 6))),
                   now=opened)
    assert pos is not None, led.last_reject_reason
    rec = led.close_manual(sym, D(str(exit_px)), now=opened + hold)
    assert rec is not None
    return rec


class FakeVps:
    """tmp_path altında `data/state` (worker) + `data/research` (motor)."""

    def __init__(self, root: Path):
        self.data = root / "data"
        self.state = self.data / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.paths = EnginePaths.for_state(self.state)
        self.futs: dict[str, FuturesLedgerV2] = {}
        self.spot_led: SpotLedger | None = None

    def fut(self, book: str = "", equity: str = "1000") -> FuturesLedgerV2:
        if book not in self.futs:
            self.futs[book] = FuturesLedgerV2(D(equity))
        return self.futs[book]

    def spot(self, cash: str = "1000") -> SpotLedger:
        if self.spot_led is None:
            self.spot_led = SpotLedger(D(cash))
        return self.spot_led

    def fut_path(self, book: str = "") -> Path:
        return (self.state / book / "futures_ledger.json") if book else (self.state / "futures_ledger.json")

    def spot_path(self) -> Path:
        return self.state / "spot_ledger.json"

    def save(self, when: datetime) -> None:
        for b, led in self.futs.items():
            led.updated_at = iso(when)
            p = self.fut_path(b)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(led.to_dict(), indent=1), encoding="utf-8")
        if self.spot_led is not None:
            self.spot_led.updated_at = iso(when)
            self.spot_path().write_text(json.dumps(self.spot_led.to_dict(), indent=1), encoding="utf-8")

    def s1a(self, when: datetime) -> dict:
        return C.run_s1a(self.paths, now=when)

    def s3(self, when: datetime) -> dict:
        return T.run_s3(self.paths, now=when)

    def night(self, when: datetime, *, save: bool = True) -> tuple[dict, dict]:
        """Bir gece: ledger'ları kaydet, S1a (arşiv + anlık görüntü), sonra S3 (günlük hedef)."""
        if save:
            self.save(when - timedelta(minutes=1))
        r1 = self.s1a(when)
        r3 = self.s3(when + timedelta(minutes=1))
        return r1, r3

    def rows(self) -> dict[str, dict]:
        return T.latest_rows(self.paths)


__all__ = ["FakeVps", "NIGHT", "UTC", "at", "night_of", "trade"]
