# -*- coding: utf-8 -*-
"""BOX THEORY V15 — DEFTER/MOTOR/PANEL ENTEGRASYONU.

Kural modülünün doğru olması yetmez: defterin 5m çerçevesini GERÇEKTEN istemesi, motorun onu GERÇEKTEN
çekmesi ve panelin kutuyu GERÇEKTEN çizmesi gerekir. Bu üçünden biri eksikse defter "çalışıyor" görünüp
hiç işlem açmaz (sessiz ölü defter). Testler bu üç bağı ayrı ayrı ve AYIRT EDİCİ biçimde sınar.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from tradingbot import paper_rules  # noqa: E402
from tradingbot.accounting import TickData  # noqa: E402
from tradingbot.agents.runner import FRAME_SPECS  # noqa: E402
from tradingbot.strategy_paper import BookSpec, StrategyBook  # noqa: E402

SYM = "ETH/USDT"
DAY = 86_400_000
M5 = 300_000

#: `leverage` KURALIN iddiasi degil, boyutlandirma zorunlulugudur: 5m stopu dar oldugu icin
#: notional = risk%/stop% buyur ve tek-coin tavanina (notional <= ozkaynak * %30 * kaldirac) carpar.
#: Bu dosyadaki `test_a_tight_intraday_stop_needs_leverage_to_fit_the_position_cap` bunu OLCER.
BOX_RULE = {"near_frac": 0.10, "trigger": "break_prev", "long_stop": "day_low",
            "exit_kind": "box_opposite", "eod_close": True, "leverage": 3}


def _ov(box_enabled: bool = True) -> dict:
    extra = [{"name": "b1_box_fade", "state_dir": "strategy_paper_box", "rule_params": dict(BOX_RULE)}] if box_enabled else []
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": extra},
                            "chart_analysis": {"enabled": False}, "news": {"enabled": False}}


def _now_ms() -> int:
    """Son 5m barı TAM kapanmış olacak şekilde bir 'şimdi': bar sınırının 30 sn sonrası."""
    return ((int(time.time() * 1000) // M5) * M5) + 30_000


def _daily_frame(now_ms: int, high: float, low: float) -> pd.DataFrame:
    """Son KAPANMIŞ günlük bar = kutu. Bugünün barı henüz kapanmadığı için seriye KONMAZ."""
    d0 = (now_ms // DAY) * DAY
    rows = []
    for i in range(6, 0, -1):
        ts = d0 - i * DAY
        rows.append({"timestamp": ts, "open": low, "high": high, "low": low, "close": (high + low) / 2,
                     "volume": 1.0})
    return pd.DataFrame(rows)


def _m5_frame(now_ms: int, bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Verilen mumlar BUGÜNÜN barları; sonuncusu `now_ms`ten hemen önce kapanmış olur."""
    last_open = ((now_ms // M5) * M5) - M5
    rows = []
    for i, (o, h, lo, c) in enumerate(bars):
        ts = last_open - (len(bars) - 1 - i) * M5
        rows.append({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0})
    return pd.DataFrame(rows)


def _prov(frames: dict, run_id: str, *, entry_ok: bool = True) -> dict:
    return {"market": "USDM_PERP", "source": "test:USDM_PERP", "entry_ok": entry_ok, "tour_id": run_id,
            "frames": {tf: {"last_ts": int(df["timestamp"].iloc[-1])} for tf, df in frames.items()}}


def _short_bars() -> list[tuple[float, float, float, float]]:
    """Tepeye değen mum + önceki mumun altına inen kırmızı mum (kutu 110/90)."""
    return [(100, 101, 99, 100), (100, 101, 99, 100), (108, 110, 107.5, 109.0), (109.0, 109.2, 107.0, 107.0)]


def _book(eng, *, rule_params: dict | None = None) -> StrategyBook:
    spec = BookSpec(name="b1_box_fade", state_dir="strategy_paper_box", starting_equity_usdt=100.0,
                    rule_params=dict(rule_params if rule_params is not None else BOX_RULE))
    b = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                     run_id="rid1", spec=spec)
    return b


def _step(book: StrategyBook, frames: dict, *, now_ms: int, price: float, entry_ok: bool = True) -> None:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    tick = TickData(last=Decimal(str(price)), mark=Decimal(str(price)), ts=now.isoformat())
    book.step(symbols=[SYM], frames_by_symbol={SYM: frames}, marks={SYM: tick}, marks_f={SYM: price},
              now=now, provenance_by_symbol={SYM: _prov(frames, book.run_id, entry_ok=entry_ok)})


# ================================================================== 1) kural kaydı
def test_the_box_book_declares_that_it_needs_five_minute_bars():
    assert paper_rules.rule_timeframes("b1_box_fade") == ("1d", "5m")
    assert paper_rules.rule_timeframes("t2_trend_regime") == ("1d",)
    assert paper_rules.needs_btc("b1_box_fade") is False, "box kuralı BTC rejimine bakmaz"
    assert paper_rules.needs_btc("t2_trend_regime") is True


# ================================================================== 2) motor bağı
def test_the_engine_fetches_five_minute_frames_only_when_a_box_book_is_enabled(tmp_path, monkeypatch):
    assert "5m" not in FRAME_SPECS, "temel çerçeve listesi 5m İÇERMEZ (ek dilim opt-in olmalı)"
    eng_off = _engine(tmp_path / "off", monkeypatch, _ov(box_enabled=False), symbols=1, equity=EQUITY)
    assert "5m" not in eng_off.runner.markets, "box defteri yokken 5m ÇEKİLMEMELİ (boşuna API yükü)"
    eng_on = _engine(tmp_path / "on", monkeypatch, _ov(box_enabled=True), symbols=1, equity=EQUITY)
    assert "5m" in eng_on.runner.markets, "box defteri açıkken motor 5m çerçevesini çekmeli"


# ================================================================== 3) defter yolu
def test_the_book_opens_a_short_when_price_fades_from_the_top_of_yesterdays_box(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, _short_bars())}
    book = _book(eng)
    _step(book, frames, now_ms=now_ms, price=107.0)
    assert book.data_checks[SYM]["ok"] is True, book.data_checks[SYM]
    pos = book.ledger.positions.get(SYM)
    assert pos is not None, "kutu tepesinde kırmızı tetikle SHORT açılmalı; retler: %s" % dict(book.rejections)
    assert pos.side.value == "SHORT"
    assert float(pos.stop) == pytest.approx(110.0), "stop önceki 5m mumunun tepesi"


def test_without_the_five_minute_frame_the_book_refuses_instead_of_trading_on_daily_data(tmp_path, monkeypatch):
    """AYIRT EDİCİ: 5m yoksa defter GÜNLÜK veriyle karar vermeye DÜŞMEZ; gerekçeli reddeder."""
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0)}
    book = _book(eng)
    _step(book, frames, now_ms=now_ms, price=107.0)
    chk = book.data_checks[SYM]
    assert chk["ok"] is False and chk["reason"] == "DATA_FRAME_MISSING_5M", chk
    assert SYM not in book.ledger.positions


def test_a_five_minute_frame_that_does_not_match_the_declared_provenance_is_refused(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, _short_bars())}
    book = _book(eng)
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    prov = _prov(frames, book.run_id)
    prov["frames"]["5m"]["last_ts"] = int(prov["frames"]["5m"]["last_ts"]) - M5      # bayat bağ
    book.step(symbols=[SYM], frames_by_symbol={SYM: frames}, marks={SYM: TickData(last=Decimal("107.0"), mark=Decimal("107.0"), ts=now.isoformat())},
              marks_f={SYM: 107.0}, now=now, provenance_by_symbol={SYM: prov})
    assert book.data_checks[SYM]["reason"] == "DATA_FRAME_MISMATCH_5M"
    assert SYM not in book.ledger.positions


def test_the_middle_of_the_box_produces_no_position(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    mid = [(100, 100.5, 99.5, 100.0)] * 4
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, mid)}
    book = _book(eng)
    _step(book, frames, now_ms=now_ms, price=100.0)
    assert book.data_checks[SYM]["ok"] is True
    assert SYM not in book.ledger.positions
    assert book.last_actions[SYM]["reason"] == "NO_SIGNAL"


def test_the_exit_choice_changes_the_recorded_target_not_the_entry(tmp_path, monkeypatch):
    """Çıkış kolu bir SEÇİMDİR: girişi ve stopu değiştirmemeli, yalnız hedefi."""
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, _short_bars())}
    out = {}
    for kind, want in (("box_opposite", 90.0), ("box_mid", 100.0)):
        b = _book(eng, rule_params=dict(BOX_RULE) | {"exit_kind": kind})
        b.key = "strategy_paper_box_%s" % kind
        b.state_dir = Path(eng.cfg.state_path) / b.key
        b.state_dir.mkdir(parents=True, exist_ok=True)
        b.ledger_path = b.state_dir / "futures_ledger.json"
        _step(b, frames, now_ms=now_ms, price=107.0)
        p = b.ledger.positions.get(SYM)
        assert p is not None, kind
        out[kind] = (float(p.entry_avg), float(p.stop), [float(t) for t in (p.targets or [])])
        assert out[kind][2] and out[kind][2][0] == pytest.approx(want), (kind, out[kind])
    assert out["box_opposite"][0] == out["box_mid"][0] and out["box_opposite"][1] == out["box_mid"][1]


# ================================================================== 4) ilan/şeffaflık
def test_the_book_declares_its_intraday_sampling_cadence(tmp_path, monkeypatch):
    """Üretim 15 dk'da bir tur atar; 5m kuralı barların 1/3'ünü görür. Bu GİZLENMEZ, özette ilan edilir."""
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    now_ms = _now_ms()
    book = _book(eng)
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, _short_bars())}
    _step(book, frames, now_ms=now_ms, price=107.0)
    book.save({SYM: 107.0}, datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc))
    doc = book.summary({SYM: 107.0}, datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)) if hasattr(book, "summary") else None
    if doc is None:
        import json
        doc = json.loads((Path(eng.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    dp = doc["data_policy"]
    assert dp["rule_timeframes"] == ["1d", "5m"]
    assert dp["intraday"] and dp["intraday"]["tf"] == "5m" and dp["intraday"]["tour_interval_min"] == 15
    assert doc["rule_family"] == "box" and doc["rule_params"]["near_frac"] == 0.10


def test_a_tight_intraday_stop_needs_leverage_to_fit_the_position_cap(tmp_path, monkeypatch):
    """YAPISAL GERÇEK: tek-coin tavanı GÜNLÜK stop için tasarlandı; 5m stopu dar olduğu için notional
    (risk%/stop%) tavana çarpar. Kaldıraç işlem başına riski DEĞİŞTİRMEZ, yalnız tavanı açar.
    Giriş 107, stop 110 → stop %2,8; risk %2 → notional özkaynağın ~%71'i; tavan %30×kaldıraç."""
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = {"1d": _daily_frame(now_ms, 110.0, 90.0), "5m": _m5_frame(now_ms, _short_bars())}
    seen = {}
    for lev in (1, 3):
        b = _book(eng, rule_params=dict(BOX_RULE) | {"leverage": lev})
        b.key = "box_lev%d" % lev
        b.state_dir = Path(eng.cfg.state_path) / b.key
        b.state_dir.mkdir(parents=True, exist_ok=True)
        b.ledger_path = b.state_dir / "futures_ledger.json"
        _step(b, frames, now_ms=now_ms, price=107.0)
        seen[lev] = (SYM in b.ledger.positions, dict(b.rejections))
    assert seen[1][0] is False and "MAX_POSITION_PCT" in seen[1][1], seen[1]
    assert seen[3][0] is True, "kaldıraç 3'te tavan açılmalı: %s" % (seen[3],)
