# -*- coding: utf-8 -*-
"""FORMASYON BOTU PROTOKOL v3 — 4h "üç beyaz asker" + RSI14>70 → LONG (laboratuvar adayı).

Gerçek `PatternBook`/`PatternScanner`/`FuturesLedgerV2`/`RiskEngine` zinciri; ağ yerine `MockProvider` (sentetik mumlar).
Parite testi: botun seçtiği sinyaller, laboratuvarın (`signal_lab`) aynı pencereyle seçtikleriyle aynıdır.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_pattern_trader_v1 as P  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3  # noqa: E402
from tradingbot.pattern_trader.strategy import PL_AWAITING, PL_CANCELLED, PL_MANAGED  # noqa: E402
from tradingbot.pattern_trader.strategy_v3 import (FAMILY_V3, PROTOCOL_V3, V3_MAX_HOLD_15M, V3_SYMBOLS,  # noqa: E402
                                                   build_plans_v3, rsi_at_close)
from tradingbot.structures.analysis import analyze  # noqa: E402

H4, H1, M15 = P.H4, P.H1, P.M15
_reset_clock = P._reset_clock      # autouse saat sıfırlama


def _series_4h(n: int, *, end_ts: int, rsi_high: bool) -> list[dict]:
    """Son 4 bar: üç beyaz asker (n-4..n-2) + onu teyit eden kapanış (n-1). `rsi_high`: öncesi güçlü yükseliş (RSI>70)
    ya da iniş-çıkış (RSI≈50)."""
    rows, px = [], 100.0
    for i in range(n):
        tail = i >= n - 4
        if tail or (rsi_high and i % 6 != 5):
            d = 1.0
        elif rsi_high:
            d = -0.4
        else:
            d = 1.0 if i % 2 == 0 else -1.0
        o, c = px, px + d
        rows.append(P._bar(end_ts - (n - i) * H4, o, max(o, c) + 0.6, min(o, c) - 0.6, c))
        px = c
    return rows


def _setup(tmp_path, *, symbol="BTC/USDT", rsi_high=True, **cfg_over):
    end = P.CLOCK[0]
    rows4h = _series_4h(80, end_ts=end, rsi_high=rsi_high)
    last = rows4h[-1]["close"]
    rows1h = P._trend_series(60, start_ts=end - 60 * H1, step=H1, px0=last - 6, drift=0.1)
    rows15 = P._trend_series(60, start_ts=end - 60 * M15, step=M15, px0=last - 1.5, drift=0.025)
    raw = symbol.replace("/", "")
    ex = P._exinfo([(raw, raw[:-4], "USDT", "PERPETUAL", "TRADING", end - 900 * P.DAY)])
    prov = P._provider({symbol: {"15m": P._df(rows15), "1h": P._df(rows1h), "4h": P._df(rows4h)}}, ex,
                       tickers={raw: P._ticker(raw, px=last)}, marks={raw: {"mark": last, "ts": end, "funding_rate": 0.0}})
    prov.books = {raw: P._book(raw, px=last)}
    prov.depths = {raw: P._depth(px=last)}
    cfg = P._cfg(tmp_path, protocol="momentum_4h_v3", **cfg_over)
    sc, book = P._scanner(cfg, prov)
    return sc, book, prov, rows4h


def test_config_accepts_only_known_protocols(tmp_path):
    assert load_v3({"pattern_trader": {"enabled": True, "protocol": "momentum_4h_v3"}}).pattern_trader.protocol == "momentum_4h_v3"
    with pytest.raises(ConfigError, match="protocol"):
        load_v3({"pattern_trader": {"enabled": True, "protocol": "bilinmeyen"}})


def test_v3_opens_a_long_on_a_fresh_three_white_soldiers_with_rsi_above_70(tmp_path):
    sc, book, prov, rows4h = _setup(tmp_path)
    assert book.protocol == "momentum_4h_v3" and "BTC/USDT" in book.allowed_symbols and len(book.allowed_symbols) == 30
    sc.scan_cycle(now_ms=P.CLOCK[0])
    pls = [pl for pl in book.plans.values() if pl["family"] == FAMILY_V3]
    assert len(pls) == 1, [pl.get("reasons") for pl in book.plans.values()]
    pl = pls[0]
    assert pl["version"] == PROTOCOL_V3 and pl["entry_tf"] == "4h" and pl["side"] == "LONG"
    assert pl["evidence"]["rsi14_at_confirm"] > 70 and pl["triggered_at_ms"] == rows4h[-1]["timestamp"] + H4
    assert "BTC/USDT" in book.ledger.positions, (pl["status"], pl.get("reasons"), pl.get("reject_detail"))
    pos = book.ledger.positions["BTC/USDT"]
    assert pl["status"] == PL_MANAGED and pos.meta["family"] == FAMILY_V3 and pos.meta["entry_tf"] == "4h"
    assert pos.meta["max_hold_bars"] == V3_MAX_HOLD_15M == 384 and pos.meta["protocol_version"] == PROTOCOL_V3
    mark = pl["rr_at_entry"]["mark"]
    assert float(pos.stop) == pytest.approx(pl["stop"])
    assert float(pos.targets[0]) == pytest.approx(mark + 2.0 * (mark - pl["stop"]), rel=1e-9), "hedef GERÇEK girişten 2R"
    assert pos.features["structure"]["name"] == "THREE_WHITE_SOLDIERS"
    # geniş 4h stop tek pozisyon tavanını aşıyordu: işlem reddedilmedi, tavana KÜÇÜLTÜLDÜ (risk bütçenin altında)
    sc_ = pos.meta.get("size_scaled_to_cap")
    assert sc_ and 0 < sc_["risk_fraction_of_budget"] < 1 and pos.leverage == 1
    assert float(pos.qty * pos.entry_avg) <= 30.001
    # aynı yapı ikinci kez işlem açmaz
    sc.scan_cycle(now_ms=P.CLOCK[0] + 60_000)
    assert sum(1 for q in book.plans.values() if q["family"] == FAMILY_V3) == 1


def test_v3_does_not_trade_when_rsi_is_not_above_70(tmp_path):
    sc, book, prov, rows4h = _setup(tmp_path, rsi_high=False)
    res = sc.scan_cycle(now_ms=P.CLOCK[0])
    assert not book.plans and not book.ledger.positions
    idx = len(rows4h) - 1
    assert rsi_at_close(rows4h, idx) < 70
    reasons = [s.get("reason") for r in (res.get("results") or []) for s in (r.get("skipped") or [])]
    assert "RSI_NOT_ABOVE_70" in reasons or not reasons, reasons


def test_v3_only_trades_the_tested_universe(tmp_path):
    sc, book, prov, rows4h = _setup(tmp_path, symbol="LNG/USDT")
    assert "LNG/USDT" not in book.allowed_symbols
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert not book.plans and not book.ledger.positions
    assert all(s != "LNG/USDT" for s, g in sc.queue(now_ms=P.CLOCK[0]) if g in ("new_listing", "rotation"))


def test_v3_cancels_pending_plans_of_the_old_protocol(tmp_path):
    sc, book, prov, rows4h = _setup(tmp_path)
    book.plans["old"] = {"plan_id": "old", "symbol": "BTC/USDT", "family": "A2_TREND_PULLBACK", "side": "LONG", "status": PL_AWAITING,
                         "version": "pattern_protocol_v2.0.0", "structure": {"pattern_id": "x"}, "status_history": [], "reasons": [],
                         "expires_at_ms": P.CLOCK[0] + 10 * H4, "trigger": {"level": 1.0, "rule": "close_above"}}
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert book.plans["old"]["status"] == PL_CANCELLED and book.plans["old"]["reasons"][-1] == "PROTOCOL_MOMENTUM_4H_V3"


def test_v3_time_stop_closes_after_96_hours(tmp_path):
    sc, book, prov, rows4h = _setup(tmp_path)
    sc.scan_cycle(now_ms=P.CLOCK[0])
    pos = book.ledger.positions["BTC/USDT"]
    mid = (float(pos.stop) + float(pos.targets[0])) / 2
    P.CLOCK[0] += 95 * H1
    P._set_mark(prov, "BTC/USDT", mid)
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert "BTC/USDT" in book.ledger.positions, "96 saat dolmadan kapanmaz"
    P.CLOCK[0] += 2 * H1
    P._set_mark(prov, "BTC/USDT", mid)
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert "BTC/USDT" not in book.ledger.positions
    assert book.ledger.history_dicts()[-1]["exit_reason"] == "TIME_STOP"


def test_parity_the_bot_selects_exactly_the_signals_the_lab_tested():
    """Aynı 240 barlık pencereyle: laboratuvarın 3WS-LONG-RSI>70 olayları == botun her kapanışta kurduğu v3 planları."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_signal_lab as T
    df = T.synth(900, seed=21)
    step = H4
    df["timestamp"] = df["timestamp"] // T.STEP * step + (T.T0 // step * step - T.T0 // T.STEP * step)
    df["close_time"] = df["timestamp"] + step - 1
    window = 240
    cfg = dataclasses.replace(L.LabConfig(), window=window, stride=1)
    lab = {e.i for e in L.catalog_events(df, "PAR/USDT", "4h", cfg)
           if e.name == "THREE_WHITE_SOLDIERS" and e.side == "LONG"}
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    bot, lab_rsi = set(), set()
    for end in range(window, len(rows) + 1):
        bars = rows[end - window:end]
        as_of = int(bars[-1]["timestamp"]) + step
        an = analyze(market="USDM_PERP", symbol="PAR/USDT", timeframe="4h", bars=bars, as_of_ms=as_of)
        plans, _ = build_plans_v3("PAR/USDT", as_of_ms=as_of, analyses={"4h": an}, bars_by_tf={"4h": bars})
        if plans:
            bot.add(end - 1)
        if end - 1 in lab:
            rsi = float(L.indicators(pd.DataFrame(bars))["rsi"][-1])
            if rsi > 70:
                lab_rsi.add(end - 1)
    assert lab and bot == lab_rsi, (sorted(bot ^ lab_rsi), len(lab))


def test_v3_symbols_are_the_lab_universe():
    assert tuple(V3_SYMBOLS) == tuple(L.WIDE_SYMBOLS) and len(V3_SYMBOLS) == 30
    assert np.isfinite(V3_MAX_HOLD_15M)
