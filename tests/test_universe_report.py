"""TESLIM RAPORU — raporun OLCTUGU, iddia ETMEDIGI.

Bir teslim raporunun en kolay bozulma bicimi, eksik veriyi varsayilanla doldurup "tamam"
demesidir. Buradaki testler tam da bunu engeller: dogrulanmamis sozlesme dogrulanmis
SAYILMAZ, olmayan seri sifir satirla degil YOK olarak gecer, ve rapor uretilmesi durumun
saglikli oldugu anlamina GELMEZ.
"""
from __future__ import annotations

import json
from pathlib import Path

from tradingbot.universe_report import (build, contracts_section, decisions_section,
                                        history_section, learning_section, render_text,
                                        resources_section)

TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def _filters(**over) -> dict:
    row = {"symbol": "BTC/USDT", "market_type": "USDM_PERP", "price_tick": "0.10",
           "qty_step": "0.001", "min_qty": "0.001", "min_notional": "50", "max_leverage": 20,
           "verified_at": "2026-09-11T00:00:00+00:00", "source": "binance_api",
           "contract_type": "PERPETUAL"}
    row.update(over)
    return {"verified_at": "2026-09-11T00:00:00+00:00", "spot": {}, "futures": {"BTC/USDT": row}}


# --------------------------------------------------------------------------- sozlesmeler
def test_unverified_contract_is_not_counted_as_verified():
    c = contracts_section(_filters(), TEN)
    assert c["verified"] == 1 and c["expected"] == 3
    assert c["missing"] == ["ETH/USDT", "SOL/USDT"]


def test_a_cached_row_from_a_default_is_not_verified():
    """Onbellekte satir OLMASI, o satirin borsadan geldigi anlamina gelmez."""
    c = contracts_section(_filters(source="default"), TEN)
    assert c["verified"] == 0 and "BTC/USDT" in c["missing"]


def test_a_non_perpetual_contract_is_not_accepted():
    c = contracts_section(_filters(contract_type="CURRENT_QUARTER"), TEN)
    assert c["verified"] == 0 and "BTC/USDT" in c["missing"]


def test_missing_cache_reports_everything_missing_not_an_empty_success():
    c = contracts_section(None, TEN)
    assert c["verified"] == 0 and c["missing"] == TEN


def test_contract_rules_are_carried_verbatim():
    c = contracts_section(_filters(), ["BTC/USDT"])
    r = c["rows"][0]
    assert r["tick_size"] == "0.10" and r["step_size"] == "0.001" and r["min_notional"] == "50"


# --------------------------------------------------------------------------- tarihsel veri
class _Manifest:
    def __init__(self, rows=0, gaps=0, first=None, last=None):
        self.row_count, self.gap_count, self.duplicate_count = rows, gaps, 0
        self.first_ts_ms, self.last_ts_ms = first, last
        self.quality_score, self.bad_chunks = 1.0, []
        self.downloaded_at, self.checksum = "2026-09-11T00:00:00+00:00", "abc123def456789"


class _Store:
    def __init__(self, have: dict):
        self.have = have

    def manifest(self, market, symbol, timeframe):
        return self.have.get((market, symbol, timeframe)) or _Manifest()


def test_absent_series_is_reported_as_absent_not_as_zero_rows():
    st = _Store({("futures", "BTC/USDT", "4h"): _Manifest(100, 0, 1_600_000_000_000, 1_700_000_000_000)})
    h = history_section(st, ["BTC/USDT", "ETH/USDT"], timeframes=("4h",))
    assert h["series_present"] == 1 and h["series_expected"] == 2
    assert h["series_absent"] == ["ETH/USDT 4h"]
    assert next(r for r in h["rows"] if r["symbol"] == "ETH/USDT")["present"] is False


def test_coverage_row_carries_utc_bounds_and_gaps():
    st = _Store({("futures", "BTC/USDT", "4h"): _Manifest(100, 7, 1_600_000_000_000, 1_700_000_000_000)})
    r = history_section(st, ["BTC/USDT"], timeframes=("4h",))["rows"][0]
    assert r["first_utc"].endswith("+00:00") and r["last_utc"].endswith("+00:00")
    assert r["gaps"] == 7 and r["rows"] == 100
    assert len(r["checksum"]) == 12


def test_gaps_are_summed_not_hidden():
    st = _Store({("futures", "BTC/USDT", "4h"): _Manifest(10, 3, 1, 2),
                 ("futures", "BTC/USDT", "1h"): _Manifest(10, 5, 1, 2)})
    h = history_section(st, ["BTC/USDT"], timeframes=("4h", "1h"))
    assert h["total_gaps"] == 8


# --------------------------------------------------------------------------- kararlar
def test_block_reason_falls_back_to_the_head_when_ranking_was_never_reached():
    risk = {"last_decisions": [{"symbol": "BTC/USDT", "block_code": "NEGATIVE_NET_EDGE"}]}
    heads = {"heads": [{"symbol": "BTC/USDT", "verdict": "FUTURES_LONG"},
                       {"symbol": "ETH/USDT", "verdict": "NO_TRADE",
                        "no_trade_reason": "NO_TRADE_LOW_CONSENSUS"}]}
    d = decisions_section(risk, heads, TEN)
    assert d["block_reasons"] == {"NEGATIVE_NET_EDGE": 1, "NO_TRADE_LOW_CONSENSUS": 1}
    assert d["verdicts"] == {"FUTURES_LONG": 1, "NO_TRADE": 1}
    assert d["universe_with_decision"] == 2 and d["universe_total"] == 3


def test_opened_list_counts_only_allowed_entries():
    risk = {"last_decisions": [{"symbol": "BTC/USDT", "risk_allowed": True},
                               {"symbol": "ETH/USDT", "risk_allowed": False}]}
    assert decisions_section(risk, {}, TEN)["opened"] == ["BTC/USDT"]


# --------------------------------------------------------------------------- ogrenme
def test_learning_section_reports_closed_and_open_counts():
    led = {"history": [{}, {}, {}], "positions": {"BTC/USDT": {}}}
    lr = learning_section(None, led)
    assert lr["closed_trades"] == 3 and lr["open_positions"] == 1
    assert lr["chain"] is None
    assert "GİRMEZ" in lr["note"]


def test_learning_chain_counters_are_carried_when_present():
    lr = learning_section({"complete": 8, "total": 10, "skipped_funding_incomplete": 2}, {})
    assert lr["chain"] == {"complete": 8, "total": 10, "skipped_funding_incomplete": 2}


# --------------------------------------------------------------------------- kaynak
def test_request_estimate_is_labelled_as_an_estimate(tmp_path: Path):
    r = resources_section(tmp_path, tmp_path, health={"seconds": 60.3, "symbols": 10},
                          llm={"calls": 0}, universe_size=10)
    assert r["requests_per_tour_estimate"] == 42
    assert r["requests_per_day_estimate_15m_cadence"] == 42 * 96
    assert "ÖLÇÜM DEĞİL" in r["estimate_basis"]
    assert r["last_tour_seconds"] == 60.3


# --------------------------------------------------------------------------- uctan uca
def test_build_survives_a_completely_empty_state(tmp_path: Path):
    """Hicbir dosya yokken de rapor uretilir; alanlar bos kalir, cokme olmaz."""
    rep = build(tmp_path / "state", tmp_path / "data", universe=TEN)
    assert rep["universe_size"] == 3
    assert rep["universe_enabled"] is False
    assert rep["contracts"]["verified"] == 0
    assert rep["decisions"]["ranked"] == 0
    assert render_text(rep)


def test_build_reads_provenance_and_render_reports_it(tmp_path: Path):
    st, dd = tmp_path / "state", tmp_path / "data"
    st.mkdir(); dd.mkdir()
    (st / "frame_provenance.json").write_text(json.dumps({
        "universe_enabled": True, "entry_universe": TEN,
        "entry_blocked_on_data": ["SOL/USDT"],
        "by_symbol": {"BTC/USDT": {"market": "USDM_PERP"}, "ETH/USDT": {"market": "USDM_PERP"},
                      "SOL/USDT": {"market": "SPOT"}}}), encoding="utf-8")
    rep = build(st, dd, universe=TEN)
    txt = render_text(rep)
    assert "2/3 sembol PERPETUAL" in txt
    assert "SOL/USDT" in txt
    assert rep["universe_enabled"] is True
