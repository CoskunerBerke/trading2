# -*- coding: utf-8 -*-
"""REPLAY STRATEJI MODU — dis kural, uretim defteri/risk/kayma yolundan acar ve kapatir (V9).

Gercek `HistoricalReplay._strategy_step` surulur (run() degil): OPEN -> risk.evaluate + ledger2.open
(boyut = islem riski / stop mesafesi), CLOSE -> ledger2.close_manual + _on_closed (result.trades).
Kotu stop reddedilir; strateji arizasi sayilir, motor durmaz.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cap_parity_and_rollback import _replay_cfg  # noqa: E402
from tradingbot.accounting import TickData  # noqa: E402
from tradingbot.history import HistoryStore  # noqa: E402
from tradingbot.replay.engine import HistoricalReplay  # noqa: E402

SYM = "ETH/USDT"
H4 = 4 * 3_600_000


def _frame(n=40, px=2000.0):
    ts = [1_700_000_000_000 + i * H4 for i in range(n)]
    return pd.DataFrame({"timestamp": ts, "open": [px] * n, "high": [px * 1.01] * n, "low": [px * 0.99] * n,
                         "close": [px] * n, "volume": [1.0] * n})


def _rep(tmp_path, strategy):
    rep = HistoricalReplay(_replay_cfg(tmp_path), run_id="strategy_probe", store=HistoryStore(tmp_path / "hist"),
                           symbols=[SYM], market="futures", tf="4h", seed=0, strategy=strategy)
    rep.frames = {SYM: {"4h": _frame()}}
    rep.primary = {SYM: rep.frames[SYM]["4h"]}
    return rep


def _marks(px):
    return {SYM: TickData(last=Decimal(str(px)), mark=Decimal(str(px)))}, {SYM: px}


def test_open_then_close_goes_through_ledger_risk_and_records_the_trade(tmp_path):
    calls = []

    def strat(sym, t, fr, pos, rp):
        calls.append((sym, pos is not None))
        if pos is None:
            return {"action": "OPEN", "direction": "LONG", "stop": 1850.0, "targets": [], "leverage": 1, "name": "T", "reason": "probe"}
        return {"action": "CLOSE", "reason": "PROBE_EXIT"}

    rep = _rep(tmp_path, strat)
    t = int(rep.frames[SYM]["4h"]["timestamp"].iloc[-1])
    now = datetime.fromtimestamp((t + H4) / 1000, tz=timezone.utc)
    marks, marks_f = _marks(2000.0)
    rep._strategy_step(t, now, marks, marks_f)
    assert SYM in rep.ledger2.positions and rep.result.n_opened == 1
    pos = rep.ledger2.positions[SYM]
    # boyut = islem riski / stop mesafesi: %2 x 50 USDT (varsayilan sermaye) / %7,5 = 13,3 USDT notional
    # (tek coine azami %30 = 15 USDT altinda; filtre yuvarlamasi payi)
    notional = float(pos.qty * pos.entry_avg)
    assert 10.0 <= notional <= 15.0, notional
    assert float(pos.stop) == 1850.0
    rep._strategy_step(t + H4, datetime.fromtimestamp((t + 2 * H4) / 1000, tz=timezone.utc), *_marks(2040.0))
    assert SYM not in rep.ledger2.positions
    assert len(rep.result.trades) == 1 and rep.result.trades[0]["exit_reason"] == "PROBE_EXIT"
    assert rep.result.trades[0]["net_pnl"] > 0                       # 2000 -> 2040, maliyet sonrasi pozitif
    assert calls == [(SYM, False), (SYM, True)]


def test_bad_stop_is_rejected_and_strategy_errors_are_counted(tmp_path):
    def bad(sym, t, fr, pos, rp):
        return {"action": "OPEN", "direction": "LONG", "stop": 2100.0}        # LONG icin stop girisin USTUNDE

    rep = _rep(tmp_path, bad)
    t = int(rep.frames[SYM]["4h"]["timestamp"].iloc[-1])
    now = datetime.fromtimestamp((t + H4) / 1000, tz=timezone.utc)
    rep._strategy_step(t, now, *_marks(2000.0))
    assert SYM not in rep.ledger2.positions
    assert rep.result.rejections["by_reason"].get("STRATEGY_BAD_STOP") == 1

    def boom(sym, t, fr, pos, rp):
        raise RuntimeError("x")

    rep2 = _rep(tmp_path / "b", boom)
    rep2._strategy_step(t, now, *_marks(2000.0))
    assert rep2.result.rejections["by_reason"].get("STRATEGY_ERROR:RuntimeError") == 1


def test_none_strategy_leaves_the_expert_path_untouched(tmp_path):
    rep = _rep(tmp_path, None)
    assert rep.strategy is None
    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "replay" / "engine.py").read_text(encoding="utf-8")
    assert "if self.strategy is not None:" in src and "self._on_closed(rec)" in src
