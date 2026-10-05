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
        # worker'ın mod dosyası (VPS'te vardır; S0 boş/yanlış state klasörünü PAPER saymaz)
        (self.state / "mode.json").write_text('{"mode": "PAPER", "live_order_path_enabled": false}', encoding="utf-8")
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


# ============================================================================ gece birimi (S0) yerine-geçenleri
SHA_APP = "a" * 40
SHA_ENGINE = "b" * 40
MEM_MAX = 536870912
#: sahte VPS'in boş disk alanı (50 GB; eşik 10 GB)
FREE_BYTES = 50 * 10**9


def fake_repo(root: Path, sha: str) -> Path:
    """`.git/HEAD` → `refs/heads/main` → sha (selfcheck.read_git_head'in okuduğu en küçük düzen)."""
    (root / ".git" / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / "main").write_text(sha + "\n", encoding="utf-8")
    return root


class Proc:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class FakeHost:
    """S0'ın dış dünyası: yalıtım denemeleri, `git merge-base`, `systemctl is-active` ve uyku. Varsayılan: sağlam bir
    gece birimi (state/market/app yazma EROFS, soket ENETUNREACH, memory.max eşit, app SHA'sı engine'in atası, yedek
    birimi boşta)."""

    def __init__(self, root: Path, *, git_rc: int = 0, backup_states: list[str] | None = None, write_status: str = "DENIED",
                 socket_status: str = "DENIED", memory_status: str = "MATCH", app_sha: str = SHA_APP,
                 engine_sha: str = SHA_ENGINE, config_text: str | None = None):
        from tradingbot.research_engine import selfcheck as SC
        self.SC = SC
        self.app = fake_repo(root / "app", app_sha)
        self.engine = fake_repo(root / "engine-app", engine_sha)
        (self.app / "config.yaml").write_text(config_text if config_text is not None else
                                              "risk: {starting_equity_usdt: 1000}\n"
                                              "learning_mode: {extra_entries: record_selectivity}\n", encoding="utf-8")
        self.git_rc, self.backup_states = git_rc, list(backup_states or ["inactive"])
        self.write_status, self.socket_status, self.memory_status = write_status, socket_status, memory_status
        self.calls: list[list[str]] = []
        self.slept: list[float] = []

    def probes(self):
        SC = self.SC

        def wd(d):
            return {"path": str(d), "status": self.write_status, "errno": "EROFS" if self.write_status == "DENIED" else None}
        return SC.Probes(write_denied=wd,
                         socket_denied=lambda: {"status": self.socket_status, "errno": "ENETUNREACH"},
                         memory_max=lambda env: {"status": self.memory_status, "expected": MEM_MAX,
                                                 "actual": MEM_MAX if self.memory_status == "MATCH" else 2 * MEM_MAX},
                         writable=lambda d: {"path": str(d), "status": "WRITABLE_OK"})

    def runner(self, cmd, **kw):
        self.calls.append(list(cmd))
        if cmd[0] == "systemctl":
            st = self.backup_states.pop(0) if len(self.backup_states) > 1 else self.backup_states[0]
            return Proc(0 if st in ("active", "activating") else 3, st + "\n")
        if cmd[0] == "git":
            return Proc(self.git_rc, "", "fatal: Not a valid commit name" if self.git_rc not in (0, 1) else "")
        raise AssertionError(f"beklenmeyen komut {cmd}")

    def sleep(self, s: float) -> None:
        self.slept.append(s)

    def kw(self) -> dict:
        """`free_bytes`: disk ölçümünün yerine-geçeni (testler sunucunun gerçek boş alanından bağımsız; gerçek `statvfs`
        yolu yalnız `test_disk_guard_real_statvfs_path_*` testinde sınanır)."""
        return {"app_dir": self.app, "engine_dir": self.engine, "probes": self.probes(), "runner": self.runner,
                "sleep": self.sleep, "env": {"ALLOW_LIVE_TRADING": "false"}, "free_bytes": FREE_BYTES}


def run_engine_night(v: FakeVps, host: FakeHost, when: datetime, *, save: bool = True, **extra) -> dict:
    """Sahte VPS'te bir tam gece (`night.run_night`): ledger'ları kaydet, sonra S0 → S1a → S3 → S7 → S7b."""
    from tradingbot.research_engine import night as N
    if save:
        v.save(when - timedelta(minutes=1))
    return N.run_night(v.paths, now=when, **{**host.kw(), **extra})


__all__ = ["FREE_BYTES", "FakeHost", "FakeVps", "MEM_MAX", "NIGHT", "Proc", "SHA_APP", "SHA_ENGINE", "UTC", "at", "fake_repo",
           "night_of", "run_engine_night", "trade"]
