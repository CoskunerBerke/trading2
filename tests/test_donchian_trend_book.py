# -*- coding: utf-8 -*-
"""4H TREND TAKİBİ (Donchian 20/10, LONG) — GÖZLEM DEFTERİ.

Kural modülü, kural kaydı, defter yolu (gerçek `StrategyBook` + `apply_action` + `FuturesLedgerV2` + `RiskEngine`) ve
laboratuvarla PARİTE: defterin seçtiği girişler ve kanal çıkışları, `signal_lab.algo_events`/`simulate_rule`ın test
ettikleriyle aynıdır.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from tradingbot import donchian_trend as D  # noqa: E402
from tradingbot import paper_rules  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402
from tradingbot.accounting import TickData  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3  # noqa: E402
from tradingbot.strategy_paper import (BAR_LAG_TOLERANCE_MS, BookSpec, StrategyBook, book_specs,  # noqa: E402
                                       validate_settings)

NAME = "d4_donchian_20_10"
SYM = "ETH/USDT"
H4 = 14_400_000


def _rows(closes: list[float], *, last_open_ms: int, rng: float = 1.0) -> list[dict]:
    """Kapanmış 4h barlar: açılış = önceki kapanış, uçlar kapanışın ±`rng` ötesi. Sonuncunun açılışı `last_open_ms`."""
    n, out, prev = len(closes), [], closes[0]
    for k, c in enumerate(closes):
        o = prev
        out.append({"timestamp": last_open_ms - (n - 1 - k) * H4, "open": o, "high": max(o, c) + rng,
                    "low": min(o, c) - rng, "close": c, "volume": 1.0})
        prev = c
    return out


def _range_then(last: list[float], n: int = 80) -> list[float]:
    """100 civarında yatay (tepe 101) + verilen son kapanışlar."""
    return [100.0 + (0.5 if k % 2 else -0.5) for k in range(n - len(last))] + list(last)


def _now_after(rows: list[dict], sec: int = 60) -> int:
    return int(rows[-1]["timestamp"]) + H4 + sec * 1000


# ================================================================== 1) kural kaydı ve ayar
def test_the_book_reads_only_four_hour_bars_and_no_btc_regime():
    assert paper_rules.rule_timeframes(NAME) == ("4h",)
    assert paper_rules.needs_btc(NAME) is False
    assert paper_rules.spec_for(NAME).family == "donchian"
    with pytest.raises(TypeError):                       # kural tanımı (20/10/2 ATR) ayardan DEĞİŞTİRİLEMEZ
        paper_rules.build_params(NAME, rule_params={"entry_n": 55})
    with pytest.raises(ValueError):
        paper_rules.build_params(NAME, rule_params={"entry_window_min": 0})


def test_the_repository_config_enables_the_observation_book_on_the_tested_coins():
    v3 = load_v3(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
    spec = next(b for b in book_specs(v3) if b.name == NAME)
    assert spec.state_dir == "strategy_paper_trend4h" and spec.enabled
    assert spec.symbols and set(spec.symbols) <= set(L.WIDE_SYMBOLS), "yalnız laboratuvarda test edilen coinler"
    assert set(spec.symbols) <= set(v3.entry_universe.symbols), "turun çerçeve çektiği evrenin dışında sembol yok"
    assert int(spec.rule_params.get("leverage", 1)) == 1
    with pytest.raises(ValueError, match="PAPER_ONLY"):
        validate_settings(enabled=True, name=NAME, app_mode="LIVE", starting_equity=200.0, atr_mult=3.0)
    with pytest.raises(ConfigError, match="rule_params"):
        load_v3({"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                    "extra": [{"name": NAME, "state_dir": "x4h", "rule_params": {"exit_n": 5}}]}})


# ================================================================== 2) kural (saf)
def test_a_fresh_close_above_the_prior_twenty_bar_high_opens_a_long_with_a_two_atr_stop():
    rows = _rows(_range_then([104.0]), last_open_ms=1_700_000_000_000 // H4 * H4)
    act = D.decide(NAME, rows=rows, now_ms=_now_after(rows))
    assert act and act["action"] == "OPEN" and act["direction"] == "LONG" and act["targets"] == []
    atr = float(L.indicators(pd.DataFrame(rows))["atr"][-1])
    assert act["atr14"] == pytest.approx(atr) and act["stop"] == pytest.approx(104.0 - 2.0 * atr)
    assert act["channel_high20"] == pytest.approx(101.5) and act["signal_close_ms"] == rows[-1]["timestamp"] + H4
    assert act["one_entry_per_signal"] and act["cap_notional_to_position_pct"] and act["risk_atr_bounds"] == [0.1, 10.0]


def test_no_entry_when_the_breakout_is_not_fresh_or_the_entry_window_has_passed():
    base = 1_700_000_000_000 // H4 * H4
    stale = _rows(_range_then([104.0, 105.0]), last_open_ms=base)       # önceki kapanış da kanalın üstündeydi
    assert D.decide(NAME, rows=stale, now_ms=_now_after(stale)) is None
    fresh = _rows(_range_then([104.0]), last_open_ms=base)
    assert D.decide(NAME, rows=fresh, now_ms=_now_after(fresh, sec=61 * 60)) is None, "kesinti sonrası geç giriş yok"
    assert D.decide(NAME, rows=fresh[-40:], now_ms=_now_after(fresh)) is None, "ısınma için yeterli bar yok"


def test_the_exit_is_a_close_below_the_prior_ten_bar_low_and_is_not_lost_during_an_outage():
    base = 1_700_000_000_000 // H4 * H4
    closes = _range_then([104.0, 106.0, 107.0, 98.0, 99.0, 99.5])       # 98: önceki 10 barın dibinin (≈99) altı
    rows = _rows(closes, last_open_ms=base)
    sig_i = len(rows) - 6
    opened = int(rows[sig_i]["timestamp"]) + H4 + 60_000                # sinyal barından sonraki barın içinde
    # çıkış barı kapanmadan: çıkış yok
    assert D.decide(NAME, rows=rows[:sig_i + 3], position={"opened_ts": opened}) is None
    act = D.decide(NAME, rows=rows[:sig_i + 4], position={"opened_ts": opened})
    assert act and act["action"] == "CLOSE" and act["reason"] == "DONCHIAN_EXIT_LOW10" and act["late_bars"] == 0
    # iki bar kaçırıldı (kesinti): çıkış yine gelir, gecikme kayda yazılır
    late = D.decide(NAME, rows=rows, position={"opened_ts": opened})
    assert late and late["action"] == "CLOSE" and late["late_bars"] == 2
    # girişten ÖNCE kapanmış bar çıkışı tetiklemez
    assert D.decide(NAME, rows=rows[:sig_i + 4], position={"opened_ts": int(rows[sig_i + 3]["timestamp"]) + H4 + 1}) is None


def test_the_position_is_closed_after_three_hundred_bars():
    base = 1_700_000_000_000 // H4 * H4
    closes = [100.0 + 0.05 * k for k in range(400)]                     # yavaş yükseliş: kanal çıkışı yok
    rows = _rows(closes, last_open_ms=base, rng=0.2)
    opened = int(rows[100]["timestamp"]) + 60_000
    assert D.decide(NAME, rows=rows[:399], position={"opened_ts": opened}) is None       # 299 bar
    act = D.decide(NAME, rows=rows[:400], position={"opened_ts": opened})                 # 300 bar
    assert act and act["reason"] == "TIME_STOP_300_BARS"


# ================================================================== 3) laboratuvarla parite
def _lab_frame(n: int, seed: int) -> pd.DataFrame:
    import test_signal_lab as T
    df = T.synth(n, seed=seed)
    df["timestamp"] = df["timestamp"] // T.STEP * H4 + (T.T0 // H4 * H4 - T.T0 // T.STEP * H4)
    df["close_time"] = df["timestamp"] + H4 - 1
    return df


@pytest.mark.parametrize("seed", [3, 11])
def test_parity_entries_stops_and_channel_exits_match_the_lab(seed):
    df = _lab_frame(1100, seed)
    ind, aux = L.indicators(df), L.aux_series(df)
    window = 300
    lab = {e.i: e for e in L.algo_events(df, "PAR/USDT", "4h", ind["atr"], aux)
           if e.name == "TREND_DONCHIAN_20_10" and e.side == "LONG" and e.i >= window - 1}
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    bot = {}
    for end in range(window, len(rows) + 1):
        act = D.decide(NAME, rows=rows[end - window:end])
        if act:
            bot[end - 1] = act
    assert lab and set(bot) == set(lab), sorted(set(bot) ^ set(lab))
    for i, act in bot.items():
        assert act["stop"] == pytest.approx(lab[i].stop, rel=1e-9, abs=1e-9)
    # kanal çıkışı: laboratuvarda KURAL ile kapanan her işlem, defterde aynı barda kapanır
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
    checked = 0
    for i, ev in lab.items():
        if L.simulate_rule(ev, arr, ind["atr"], aux, L.LabConfig()) or ev.exit_reason != "RULE":
            continue
        j = i + 1
        opened = int(rows[j]["timestamp"]) + 60_000
        k_lab = j + ev.hold - 2                                          # kuralın tetiklendiği kapanış barı
        k_bot = next(k for k in range(j, len(rows))
                     if D.decide(NAME, rows=rows[max(0, k + 1 - window):k + 1], position={"opened_ts": opened}))
        assert k_bot == k_lab, (i, k_bot, k_lab)
        checked += 1
    assert checked >= 5


# ================================================================== 4) defter yolu (gerçek zincir)
def _ov(extra: list | None = None) -> dict:
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                               "extra": extra if extra is not None else
                                               [{"name": NAME, "state_dir": "strategy_paper_trend4h"}]},
                            "chart_analysis": {"enabled": False}, "news": {"enabled": False}}


def _book(eng) -> StrategyBook:
    spec = BookSpec(name=NAME, state_dir="strategy_paper_trend4h", starting_equity_usdt=200.0, rule_params={"leverage": 1})
    return StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                        run_id="rid1", spec=spec)


def _frames(now_ms: int, last: list[float]) -> dict:
    last_open = (now_ms // H4) * H4 - H4
    return {"4h": pd.DataFrame(_rows(_range_then(last), last_open_ms=last_open))}


def _step(book: StrategyBook, frames: dict, *, now_ms: int, price: float) -> None:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    prov = {"market": "USDM_PERP", "source": "test:USDM_PERP", "entry_ok": True, "tour_id": book.run_id,
            "frames": {tf: {"last_ts": int(df["timestamp"].iloc[-1])} for tf, df in frames.items()}}
    book.step(symbols=[SYM], frames_by_symbol={SYM: frames},
              marks={SYM: TickData(last=Decimal(str(price)), mark=Decimal(str(price)), ts=now.isoformat())},
              marks_f={SYM: price}, now=now, provenance_by_symbol={SYM: prov})


def _now_ms() -> int:
    return (int(time.time() * 1000) // H4) * H4 + 120_000


def test_the_engine_builds_the_book_and_fetches_its_four_hour_frames(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    keys = [b.key for b in eng.strategy_books]
    assert "strategy_paper_trend4h" in keys
    assert "4h" in eng._book_timeframes and "4h" in eng.runner.markets
    assert BAR_LAG_TOLERANCE_MS["4h"] == 900_000


def test_the_book_opens_a_long_scaled_to_the_position_cap_and_never_twice_on_one_signal(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = _frames(now_ms, [104.0])
    book = _book(eng)
    _step(book, frames, now_ms=now_ms, price=104.1)
    assert book.data_checks[SYM]["ok"] is True, book.data_checks[SYM]
    pos = book.ledger.positions.get(SYM)
    assert pos is not None, dict(book.rejections)
    assert pos.side.value == "LONG" and pos.leverage == 1 and not pos.targets
    atr = float(L.indicators(frames["4h"])["atr"][-1])
    assert float(pos.stop) == pytest.approx(104.0 - 2.0 * atr)
    assert pos.meta["signal"]["lab_algo"] == "TREND_DONCHIAN_20_10"
    scaled = pos.meta.get("size_scaled_to_cap")
    assert scaled and 0 < scaled["risk_fraction_of_budget"] < 1, "geniş stop tavanı aşıyordu: küçültülmeli, reddedilmemeli"
    # stop aynı barda geldi (defter kapattı) → sonraki tur aynı sinyali yeniden görür ama GİRMEZ
    book.ledger.close_manual(SYM, float(pos.stop), reason="STOP", now=datetime.fromtimestamp(now_ms / 1000 + 300, tz=timezone.utc))
    _step(book, frames, now_ms=now_ms + 900_000, price=104.0)
    assert SYM not in book.ledger.positions
    assert book.last_actions[SYM]["reason"] == "SIGNAL_ALREADY_USED"


def test_an_entry_price_too_close_to_the_stop_is_outside_the_tested_range(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = _frames(now_ms, [104.0])
    atr = float(L.indicators(frames["4h"])["atr"][-1])
    book = _book(eng)
    _step(book, frames, now_ms=now_ms, price=104.0 - 1.95 * atr)          # mesafe 0,05 ATR < 0,1 ATR
    assert SYM not in book.ledger.positions
    assert book.rejections.get("RISK_OUTSIDE_TESTED_RANGE") == 1, dict(book.rejections)


def test_the_book_closes_on_the_channel_exit(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    book = _book(eng)
    _step(book, _frames(now_ms - 3 * H4, [104.0]), now_ms=now_ms - 3 * H4, price=104.1)
    assert SYM in book.ledger.positions, dict(book.rejections)
    stop = float(book.ledger.positions[SYM].stop)
    frames = _frames(now_ms, [104.0, 105.0, 104.5, 98.2])               # son kapanış önceki 10 barın dibinin (98,5) altında
    assert 98.2 > stop, "çıkış stop'tan değil KANALDAN gelmeli"
    _step(book, frames, now_ms=now_ms, price=98.3)
    assert SYM not in book.ledger.positions, (book.last_actions.get(SYM), dict(book.rejections))
    assert book.ledger.history[-1].exit_reason == "DONCHIAN_EXIT_LOW10"


def test_rule_state_is_readable_for_the_panel():
    rows = _rows(_range_then([104.0]), last_open_ms=1_700_000_000_000 // H4 * H4)
    rs = paper_rules.state_from_rows(NAME, daily=[], intraday=rows, btc_rows=[],
                                     params=paper_rules.build_params(NAME, rule_params={}))
    assert rs["ok"] and rs["fresh_breakout"] is True and rs["stop_if_open"] < 104.0 and "KANITLANMADI" in rs["evidence"]
    assert np.isfinite(rs["channel_low10"])
