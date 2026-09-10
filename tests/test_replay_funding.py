"""REPLAY FUNDING — sifirin "olculdu" mu "hic sorulmadi" mi oldugunu ayiran testler.

Bulunan kusur (2026-09-11): `replay/engine.py` `ledger2.tick(...)` cagrisina
`funding_rate_lookup` HIC vermiyordu. `FuturesLedgerV2.tick` icin bu parametre istege
bagli oldugundan funding maliyeti her replay kapanisinda YAPISAL olarak 0 cikiyordu.
23 islemlik SOL replay'inde 23 kapanisin 23'u `funding=0.0` tasiyordu ve bu sonuclar
"maliyet sonrasi" gibi okunabiliyordu.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from tradingbot.replay.funding_archive import DEFAULT_TOLERANCE_MS, ArchiveFundingRates


class _Store:
    """Arsiv taklidi: yalniz `read(market, symbol, timeframe)` sunar."""

    def __init__(self, series: dict):
        self.series = series
        self.reads: list[tuple] = []

    def read(self, market, symbol, timeframe):
        self.reads.append((market, symbol, timeframe))
        df = self.series.get((market, symbol, timeframe))
        if df is None:
            return pd.DataFrame(columns=["timestamp", "rate", "mark"])
        return df


def _series(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "rate", "mark"])


def _when(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


BASE = 1_735_689_600_000          # 2025-01-01T00:00:00Z
H8 = 8 * 3_600_000


# --------------------------------------------------------------------------- kaynak sozlesmesi
def test_replay_actually_passes_a_funding_lookup_to_the_ledger():
    """KUSURUN REGRESYON TESTI: cagri lookup'siz yapilirsa funding sessizce 0 olur."""
    from tradingbot.replay.engine import HistoricalReplay
    src = inspect.getsource(HistoricalReplay._advance)
    assert "funding_rate_lookup" in src, (
        "ledger2.tick funding lookup ALMIYOR — funding maliyeti yapisal olarak 0 cikar")
    assert "self.funding_rates" in src


def test_replay_ledger_does_not_fall_back_to_the_last_known_rate():
    """Bir gunluk boslugu son bilinen oranla doldurmak sessiz bir tahmindir."""
    from tradingbot.replay.engine import HistoricalReplay
    src = inspect.getsource(HistoricalReplay.__init__)
    assert "fallback_to_last_known=False" in src


# --------------------------------------------------------------------------- arama
def test_exact_settlement_returns_the_archived_rate():
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE, 0.0001, 100.0),
                                                              (BASE + H8, -0.00005, 101.0)])})
    f = ArchiveFundingRates(st)
    assert f.rate_at("SOL/USDT", _when(BASE)) == Decimal("0.0001")
    assert f.rate_at("SOL/USDT", _when(BASE + H8)) == Decimal("-0.00005")
    assert f.coverage()["unknown"] == 0


def test_sub_second_timestamp_drift_is_tolerated():
    """Binance settlement damgalari milisaniye kayar (ornek: +3 ms)."""
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE + 3, 0.0001, 100.0)])})
    assert ArchiveFundingRates(st).rate_at("SOL/USDT", _when(BASE)) == Decimal("0.0001")


def test_a_missing_settlement_is_unknown_not_zero():
    """En onemli kural: cevap yoksa `None` doner. `None` != 0."""
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE, 0.0001, 100.0)])})
    f = ArchiveFundingRates(st)
    assert f.rate_at("SOL/USDT", _when(BASE + H8)) is None
    cov = f.coverage()
    assert cov["unknown"] == 1 and cov["complete"] is False


def test_a_neighbouring_hour_is_not_mistaken_for_a_settlement():
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE, 0.0001, 100.0)])})
    f = ArchiveFundingRates(st, tolerance_ms=DEFAULT_TOLERANCE_MS)
    assert f.rate_at("SOL/USDT", _when(BASE + 3_600_000)) is None


def test_a_symbol_with_no_series_is_unknown_and_named():
    f = ArchiveFundingRates(_Store({}))
    assert f.rate_at("AAVE/USDT", _when(BASE)) is None
    cov = f.coverage()
    assert cov["missing_series"] == ["AAVE/USDT"]
    assert cov["complete"] is False


def test_a_broken_store_is_unknown_not_zero():
    class _Broken:
        def read(self, *_a):
            raise RuntimeError("parquet bozuk")

    f = ArchiveFundingRates(_Broken())
    assert f.rate_at("SOL/USDT", _when(BASE)) is None
    assert f.coverage()["unknown"] == 1


def test_series_is_read_once_per_symbol():
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE + i * H8, 0.0001, 100.0)
                                                              for i in range(50)])})
    f = ArchiveFundingRates(st)
    for i in range(50):
        f.rate_at("SOL/USDT", _when(BASE + i * H8))
    assert st.reads.count(("futures", "SOL/USDT", "funding")) == 1
    assert f.coverage()["answered"] == 50


def test_binary_search_finds_the_right_row_in_a_long_series():
    rows = [(BASE + i * H8, round(0.0001 * (i + 1), 8), 100.0) for i in range(1000)]
    f = ArchiveFundingRates(_Store({("futures", "BTC/USDT", "funding"): _series(rows)}))
    for i in (0, 1, 499, 998, 999):
        assert f.rate_at("BTC/USDT", _when(BASE + i * H8)) == Decimal(str(rows[i][1]))


def test_coverage_reports_the_fraction_and_the_source():
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE, 0.0001, 100.0)])})
    f = ArchiveFundingRates(st)
    f.rate_at("SOL/USDT", _when(BASE))
    f.rate_at("SOL/USDT", _when(BASE + H8))
    cov = f.coverage()
    assert cov["queries"] == 2 and cov["answered"] == 1
    assert cov["answered_fraction"] == 0.5
    assert "fundingRate" in cov["source"]


def test_callable_interface_matches_the_ledger_rate_lookup_contract():
    """`RateLookup = Callable[[str, datetime], Decimal|float|str|None]`."""
    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE, 0.0001, 100.0)])})
    f = ArchiveFundingRates(st)
    assert f("SOL/USDT", _when(BASE)) == Decimal("0.0001")


# --------------------------------------------------------------------------- defter entegrasyonu
def test_ledger_actually_charges_funding_when_the_lookup_answers():
    """Uctan uca: lookup cevap verirse pozisyon funding ODER; vermezse odemez."""
    from tradingbot.accounting import (AmountType, FuturesLedgerV2, LiquidationParams, SizeSpec,
                                       TickData, default_brackets)
    from tradingbot.accounting.funding import FundingSchedule

    st = _Store({("futures", "SOL/USDT", "funding"): _series([(BASE + H8, 0.001, 100.0)])})
    rates = ArchiveFundingRates(st)

    def _ledger():
        return FuturesLedgerV2(1000.0, max_positions=3, brackets=default_brackets(),
                               liq_params=LiquidationParams(), funding=FundingSchedule(fallback_to_last_known=False))

    opened = datetime.fromtimestamp(BASE / 1000, tz=timezone.utc)
    later = datetime.fromtimestamp((BASE + H8 + 60_000) / 1000, tz=timezone.utc)
    marks = {"SOL/USDT": TickData(last=Decimal("100"), mark=Decimal("100"))}

    led = _ledger()
    led.open("SOL/USDT", "LONG", 100.0, SizeSpec(Decimal("200"), AmountType.NOTIONAL, 2), now=opened)
    led.tick(marks, now_utc=later, funding_rate_lookup=rates, bar_advance=True)
    charged = float(led.positions["SOL/USDT"].funding_paid or 0)

    led2 = _ledger()
    led2.open("SOL/USDT", "LONG", 100.0, SizeSpec(Decimal("200"), AmountType.NOTIONAL, 2), now=opened)
    led2.tick(marks, now_utc=later, bar_advance=True)          # ESKI davranis: lookup YOK
    silent = float(led2.positions["SOL/USDT"].funding_paid or 0)

    assert charged > 0, "arsivde oran varken funding ODENMEDI"
    assert silent == 0, "lookup'siz cagri sessiz sifir uretmeli (kusurun kendisi)"
    assert charged != silent


def test_replay_result_exposes_funding_coverage():
    from tradingbot.replay.engine import ReplayResult
    r = ReplayResult(run_id="x", seed=0, symbols=[], market="futures", tf="4h", start_ms=0, end_ms=1)
    assert "funding_coverage" in r.to_dict()
