# -*- coding: utf-8 -*-
"""ÖĞRENME MODU L1 — STRATEJİ DEFTERLERİ (T2, M2, Box, D4, C4) (2026-09-28, öğrenme modu).

Kapsam: `apply_action(learning=…)` (slot boyutu, öğrenme RiskEngine'i, çağrı başına allow_shrink, etiketler),
`StrategyBook.step(learning=…)` (tembel öğrenme RiskEngine'i ve karşı-olgusal kayıtçı, Box öğrenme min_stop, T2/M2 yapı
GİRİŞİ gölgesi + yönetim ENFORCE, C4 also_matched / açık pozisyon karşı-olgusalları, D4/C4 genişletilmiş evren etiketi),
Box zamanlayıcısının geçiş başına öğrenme görünümü. Anahtar KAPALIYKEN (None / kapalı görünüm / askıda) her defter
ailesinin bit-aynı kaldığı ayrıca kanıtlanır; ölçüm kapıları (veri, stop, sinyal tekrarı, test edilmiş aralık, sembol başına
tek pozisyon) öğrenme modunda da AYNEN durdurur.
"""
from __future__ import annotations

import ast
import copy
import inspect
import json
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from candle_variation_examples import EXAMPLES  # noqa: E402
from structure_fixtures import (DAY, breakout_bar, neutral_trend, pole_and_consolidation,  # noqa: E402
                                swing_high_then_sweep)
from test_candle_variations_book import CV0, VX, VY, _rows_at, registry  # noqa: E402

from tradingbot import candle_book, paper_rules  # noqa: E402
from tradingbot.accounting import FeeSchedule, FuturesLedgerV2, SlippageModel, TickData  # noqa: E402
from tradingbot.accounting.filters import FiltersCache  # noqa: E402
from tradingbot.accounting.models import MarketType, SymbolFilters  # noqa: E402
from tradingbot.box_timer import BoxTimer  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.learning_mode import SIZE_SHRUNK, SIZE_SLOT, BookLearning, LearningMode, profile_for  # noqa: E402
from tradingbot.risk import RiskEngine, build_state  # noqa: E402
from tradingbot.risk.killswitch import KillSwitch  # noqa: E402
from tradingbot.risk.profiles import PROFILES  # noqa: E402
from tradingbot.strategy_paper import CF_FILE, BookSpec, DataVerdict, StrategyBook, apply_action  # noqa: E402

assert registry  # fikstür (içe aktarılarak bu modülde de kullanılır)

UTC = timezone.utc
SYM, BTC, RUN = "ETH/USDT", "BTC/USDT", "RUN-1"
H4, M5 = 14_400_000, 300_000
T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY
PAPER = PROFILES["PAPER_RESEARCH"]
#: sabit karar anları (duvar saatinden bağımsız → bayt karşılaştırması mümkün)
NOW_H4 = int(datetime(2026, 9, 28, 8, 2, tzinfo=UTC).timestamp() * 1000)
NOW_M5 = int(datetime(2026, 9, 28, 10, 5, 30, tzinfo=UTC).timestamp() * 1000)
#: duvar saatiyle yazılan alanlar (ret anı `_reject`, defterin yazma anı) — karşılaştırmadan düşer
VOLATILE = {"at", "updated_at", "generated_at", "recorded_at", "rule_stale_s"}
RULE = {"t2_trend_regime": {"leverage": 2, "leverage_max": 4}, "m2_tsmom28": {"leverage": 2, "leverage_max": 4},
        "b1_box_fade": {"near_frac": 0.10, "trigger": "break_prev", "long_stop": "day_low", "exit_kind": "box_opposite",
                        "min_stop_pct": 2.22, "eod_close": True, "leverage": 3, "leverage_max": 4},
        "d4_donchian_20_10": {"leverage": 1}}
#: config.yaml L1 değerleri (CONTRACT)
LEARN = {"t2_trend_regime": {"slots": 40, "leverage_max": 4}, "m2_tsmom28": {"slots": 40, "leverage_max": 4},
         "b1_box_fade": {"slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
         "d4_donchian_20_10": {"slots": 20, "leverage_max": 3, "symbols": "universe"},
         "c4_candle_variations": {"slots": 20, "leverage_max": 3, "symbols": "universe"}}


# ============================================================================ yardımcılar
def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _bl(name: str, **over) -> BookLearning:
    d = dict(on=True, name=name, slots=20, leverage_max=3, risk_pct=0.5, reserve_pct=5.0, liq_buffer_mult=2.0,
             min_notional_bump=True, counterfactual=True, max_pending=2000, min_stop_pct=None, symbols=None)
    d.update(LEARN.get(name, {}))
    d.update(over)
    return BookLearning(**d)


def _section(**books):
    return types.SimpleNamespace(enabled=True, risk_per_trade_pct=0.5, max_total_open_risk_pct=100.0, margin_reserve_pct=5.0,
                                 liq_buffer_mult=2.0, min_notional_bump=True, counterfactual=True,
                                 counterfactual_max_pending=2000, books=books,
                                 strategy_overrides={"structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"]})


def _book(tmp: Path, name: str, *, mode: str = "OFF", rule_params: dict | None = None, symbols=None, filters=(),
          equity: float = 200.0) -> StrategyBook:
    cfg = BotConfig()
    cfg.project_root = tmp
    cfg.obsidian.vault_path = str(tmp / "vault")
    cfg.v3 = load_v3({"structures": {"enabled": True, "t2_trend_regime": mode, "m2_tsmom28": mode, "b1_box_fade": mode}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    fc = FiltersCache(cfg.cache_path / "symbol_filters.json")
    for f in filters:
        fc.put(f)
    spec = BookSpec(name=name, state_dir="sp_" + name, starting_equity_usdt=equity, symbols=symbols,
                    rule_params=dict(RULE.get(name, {}) if rule_params is None else rule_params))
    return StrategyBook(cfg, profile=PAPER, killswitch=KillSwitch(), filters_cache=fc, run_id=RUN, spec=spec)


def _prov(fbs: dict, run_id: str) -> dict:
    return {s: {"market": "USDM_PERP", "source": "test:USDM_PERP", "entry_ok": True, "tour_id": run_id,
                "frames": {tf: {"last_ts": int(df["timestamp"].iloc[-1])} for tf, df in fr.items()}} for s, fr in fbs.items()}


def _step(book: StrategyBook, fbs: dict, *, now_ms: int, px, symbols=None, prov=None, **kw) -> None:
    syms = list(symbols) if symbols is not None else [s for s in fbs if s != BTC]
    pxs = dict(px) if isinstance(px, dict) else {s: float(px) for s in syms}
    now = _dt(now_ms)
    marks = {s: TickData(last=Decimal(str(p)), mark=Decimal(str(p)), ts=now.isoformat()) for s, p in pxs.items()}
    book.step(symbols=syms, frames_by_symbol=fbs, marks=marks, marks_f=pxs, now=now,
              provenance_by_symbol=_prov(fbs, book.run_id) if prov is None else prov, data_gaps={}, **kw)
    book.save(pxs, now)


def _norm(x):
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def _snapshot(book: StrategyBook) -> tuple[str, str, list[str]]:
    led = json.loads(book.ledger_path.read_text(encoding="utf-8"))
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    return (json.dumps(_norm(led)), json.dumps(_norm(summ)), sorted(p.name for p in book.state_dir.iterdir()))


# ---- T2 / M2 (1d) : bayrak ve kırılış (structure_fixtures)
def _trend_rows():
    base = neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3)
    flag = pole_and_consolidation(base, step=DAY)
    brk = breakout_bar(flag, step=DAY, above=max(r["high"] for r in flag[-5:]))
    return flag, brk


def _trend_fbs(rows, syms=(SYM,)) -> dict:
    btc = neutral_trend(len(rows), start_ms=T0, step=DAY, up=True, scale=0.3)
    out = {s: {"1d": pd.DataFrame(rows)} for s in syms}
    out[BTC] = {"1d": pd.DataFrame(btc)}
    return out


def _asof(rows, step: int = DAY) -> int:
    return int(rows[-1]["timestamp"]) + step + 60_000


# ---- D4 (4h): 20 barlık kanalın taze kırılımı
def _d4_fbs(syms=(SYM,), last=(104.0,), now_ms: int = NOW_H4) -> dict:
    closes = [100.0 + (0.5 if k % 2 else -0.5) for k in range(80 - len(last))] + list(last)
    last_open = (now_ms // H4) * H4 - H4
    rows, prev = [], closes[0]
    for k, c in enumerate(closes):
        rows.append({"timestamp": last_open - (len(closes) - 1 - k) * H4, "open": prev, "high": max(prev, c) + 1.0,
                     "low": min(prev, c) - 1.0, "close": c, "volume": 1.0})
        prev = c
    return {s: {"4h": pd.DataFrame(rows)} for s in syms}


# ---- Box (1d kutu 110/90 + bugünün 5m mumları)
BOX_WIDE = [(100, 101, 99, 100), (100, 101, 99, 100), (108, 110, 107.5, 109.0), (109.0, 109.2, 107.0, 107.0)]  # stop 110 (%2,8)
BOX_TIGHT = [(100, 101, 99, 100), (100, 101, 99, 100), (108.5, 109.6, 108.4, 109.3), (109.3, 109.4, 108.2, 108.3)]  # %1,2


def _box_fbs(bars, now_ms: int = NOW_M5, syms=(SYM,)) -> dict:
    d0 = (now_ms // DAY) * DAY
    daily = [{"timestamp": d0 - i * DAY, "open": 90.0, "high": 110.0, "low": 90.0, "close": 100.0, "volume": 1.0}
             for i in range(6, 0, -1)]
    last_open = (now_ms // M5) * M5 - M5
    m5 = [{"timestamp": last_open - (len(bars) - 1 - i) * M5, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
          for i, (o, h, lo, c) in enumerate(bars)]
    return {s: {"1d": pd.DataFrame(daily), "5m": pd.DataFrame(m5)} for s in syms}


# ---- C4 (4h, 500 bar): örnek varyasyonun eşleştiği pencere
def _c4_book(tmp: Path, ids: list[str], symbols=(SYM,)) -> StrategyBook:
    return _book(tmp, "c4_candle_variations", symbols=list(symbols),
                 rule_params={"leverage": 1, "entry_window_min": 60, "variations": list(ids)})


def _c4_fbs(now_ms: int = NOW_H4) -> dict:
    return {SYM: {"4h": pd.DataFrame(_rows_at(EXAMPLES[CV0]["match"], now_ms))}}


def _scenario(name: str, tmp: Path, c4_ids: list[str] | None) -> tuple[StrategyBook, dict, int, float]:
    """(defter, çerçeveler, karar anı, fiyat) — her ailede baseline'da bir OPEN üreten senaryo (C4: kayıtlı kimlikler)."""
    if name in ("t2_trend_regime", "m2_tsmom28"):
        _flag, brk = _trend_rows()
        return _book(tmp, name, mode="ENFORCE"), _trend_fbs(brk), _asof(brk), float(brk[-1]["close"])
    if name == "b1_box_fade":
        return _book(tmp, name, mode="SHADOW"), _box_fbs(BOX_WIDE), NOW_M5, 107.0
    if name == "d4_donchian_20_10":
        return _book(tmp, name, symbols=[SYM]), _d4_fbs(), NOW_H4, 104.1
    return _c4_book(tmp, list(c4_ids or [])), _c4_fbs(), NOW_H4, 96.6


FAMILIES = ("t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10", "c4_candle_variations")


# ============================================================================ 1) anahtar KAPALI → bit-aynı
@pytest.mark.parametrize("name", FAMILIES)
def test_off_parity_every_family_is_byte_identical_and_writes_nothing_new(name, tmp_path, registry):
    """None, `structures_entry_shadow=True` ama öğrenme yok, kapalı/askıdaki LearningMode'un `book()`u (None) ve `on=False`
    görünüm: defter JSON'u, özet dosyası ve dizin içeriği anahtarsız çağrıyla AYNI; karşı-olgusal dosyası ve özet alanı YOK."""
    disabled = LearningMode(types.SimpleNamespace(enabled=False, books={name: {"enabled": True}}), mode_gate=lambda: (True, "OK"))
    suspended = LearningMode(_section(**{name: {"enabled": True}}), mode_gate=lambda: (False, "MODE_NOT_PAPER"))
    for lm in (disabled, suspended):
        lm.refresh()
        assert lm.book(name) is None
    variants = {"no_kw": {}, "none": {"learning": None, "structures_entry_shadow": True},
                "disabled": {"learning": disabled.book(name)}, "suspended": {"learning": suspended.book(name)},
                "view_off": {"learning": _bl(name, on=False), "structures_entry_shadow": True}}
    c4_ids = registry(VX, VY) if name == "c4_candle_variations" else None
    snaps = {}
    for key, kw in variants.items():
        book, fbs, now_ms, px = _scenario(name, tmp_path / key, c4_ids)
        _step(book, fbs, now_ms=now_ms, px=px, **kw)
        _step(book, fbs, now_ms=now_ms + 900_000, px=px, **kw)          # aynı bar, ikinci tur
        assert book.ledger.positions, "önkoşul: baseline senaryosu gerçekten açar"
        assert book.cf is None and book.risk_learning is None and book.learning is None
        assert not (book.state_dir / CF_FILE).exists()
        summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
        assert "learning" not in summ
        for p in book.ledger.positions.values():
            assert "learning" not in p.features and "learning" not in p.meta
        snaps[key] = _snapshot(book)
    ref = snaps.pop("no_kw")
    for key, snap in snaps.items():
        assert snap == ref, key


@pytest.mark.parametrize("act", [
    {"action": "OPEN", "direction": "LONG", "stop": 94.0, "leverage": 2, "leverage_max": 4, "name": "t2"},
    {"action": "OPEN", "direction": "LONG", "stop": 99.0, "leverage": 1, "name": "d4", "cap_notional_to_position_pct": True,
     "risk_atr_bounds": [0.1, 10], "atr14": 0.8, "one_entry_per_signal": True, "signal_ts": 1, "signal_close_ms": 2},
    {"action": "OPEN", "direction": "SHORT", "stop": 102.0, "leverage": 1, "name": "c4", "target_r_from_entry": 2.0,
     "cap_notional_to_position_pct": True, "risk_atr_bounds": [0.1, 10], "atr14": 1.0, "variation": {"id": "CV1"}},
    {"action": "OPEN", "direction": "SHORT", "stop": 102.8, "leverage": 3, "leverage_max": 4, "name": "box", "targets": [90.0]},
])
def test_apply_action_without_learning_keeps_the_baseline_size_exactly(act):
    """`learning` yok / None / on=False → AYNI sonuç; boyut taban formülüyle (profil %2 × equity / stop, ihtiyaç kadar
    kaldıraç, isteyen eylemde tavana küçültme)."""
    out = []
    for kw in ({}, {"learning": None}, {"learning": _bl("t2_trend_regime", on=False)}):
        led, risk, st, now = _ledger_env()
        a = copy.deepcopy(act)
        rej: list = []
        res = apply_action(a, symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=risk, profile=PAPER, state=st,
                           filters=None, run_id=RUN, reject=lambda s, r: rej.append(r), on_closed=lambda r: None,
                           data=_verdict(), **kw)
        out.append((res, rej, a, json.dumps(led.to_dict(), default=str)))
    assert out[0] == out[1] == out[2]
    res, _rej, a, _led = out[0]
    assert res == "OPENED" and "_learning" not in a
    pos = json.loads(out[0][3])["positions"][SYM]
    risk_usdt = 0.02 * 200.0
    notional = risk_usdt / (abs(100.0 - act["stop"]) / 100.0)
    lev = int(act.get("leverage") or 1)
    if int(act.get("leverage_max") or 0) > lev:
        lev = max(lev, min(-(-notional // 60.0), act["leverage_max"]))
    if act.get("cap_notional_to_position_pct") and notional > 60.0 * lev:
        notional = 60.0 * lev * 0.999
    assert pos["leverage"] == int(lev) and float(pos["requested_notional"]) == pytest.approx(notional, abs=1e-4)


def _ledger_env(equity: float = 200.0, wallet: float | None = None):
    led = FuturesLedgerV2(starting_equity=equity, fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                          slippage=SlippageModel(fixed_bps=Decimal("3")))
    if wallet is not None:
        led.wallet_balance = Decimal(str(wallet))
    now = datetime(2026, 9, 28, 10, 7, 30, tzinfo=UTC)
    fs = led.summary({})
    st = build_state(equity=float(fs["equity_mtm"]), starting_equity=equity, available=float(fs["available"]),
                     used_margin=float(fs["used_margin"]), positions=[], history=[], high_water_mark=0.0, now=now)
    return led, RiskEngine(PAPER, KillSwitch()), st, now


def _verdict() -> DataVerdict:
    return DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="test", tour_id=RUN, bars={"4h": 1})


def test_learning_is_opt_in_keyword_with_none_default_and_replay_never_passes_it():
    assert inspect.signature(apply_action).parameters["learning"].default is None
    sig = inspect.signature(StrategyBook.step).parameters
    assert sig["learning"].default is None and sig["structures_entry_shadow"].default is False
    assert inspect.signature(BoxTimer).parameters["learning"].default is None
    tree = ast.parse((ROOT / "tradingbot" / "replay" / "engine.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "apply_action"]
    assert len(calls) >= 2 and all(k.arg != "learning" for c in calls for k in c.keywords), "replay learning=None kalmalı"


# ============================================================================ 2) limitler kalkar
@pytest.mark.parametrize("name", ["t2_trend_regime", "d4_donchian_20_10"])
def test_twelve_valid_opens_all_open_at_half_percent_risk(name, tmp_path):
    """Baseline 3 civarında TOTAL_OPEN_RISK / INSUFFICIENT_MARGIN ile durur; öğrenmede 12 geçerli sinyalin HEPSİ açılır,
    işlem başı risk ≤ %0,5 × E, Σ marj ≤ %95 × E, ne TOTAL_OPEN_RISK ne INSUFFICIENT_MARGIN ne MAX_POSITION_PCT."""
    syms = ["S%02d/USDT" % i for i in range(12)]
    if name == "t2_trend_regime":
        _flag, brk = _trend_rows()
        fbs, now_ms, px = _trend_fbs(brk, syms), _asof(brk), float(brk[-1]["close"])
    else:
        fbs, now_ms, px = _d4_fbs(syms), NOW_H4, 104.1
    base = _book(tmp_path / "base", name, symbols=(syms if name == "d4_donchian_20_10" else None))
    _step(base, fbs, now_ms=now_ms, px=px, symbols=syms)
    assert len(base.ledger.positions) <= 3, "önkoşul: baseline kapasiteye takılır"
    assert set(base.rejections) & {"TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN"}, base.rejections

    book = _book(tmp_path / "learn", name, symbols=(syms if name == "d4_donchian_20_10" else None))
    _step(book, fbs, now_ms=now_ms, px=px, symbols=syms, learning=_bl(name))
    assert set(book.ledger.positions) == set(syms), book.rejections
    assert not set(book.rejections) & {"TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN", "MAX_POSITION_PCT"}
    E = 200.0
    margin = 0.0
    for s, p in book.ledger.positions.items():
        lr = p.meta["learning"]
        assert lr == p.features["learning"]
        assert lr["size_rule"] == SIZE_SLOT and lr["slots"] == LEARN[name]["slots"] and lr["risk_pct"] == 0.5
        assert lr["risk_usdt"] <= 0.005 * E + 1e-9 and 0 < lr["risk_fraction_of_budget"] <= 1.0 + 1e-9
        assert 1 <= p.leverage <= LEARN[name]["leverage_max"]
        assert (1.0 / p.leverage - 0.004) >= 2.0 * abs(float(p.entry_avg) - float(p.stop)) / float(p.entry_avg)
        margin += float(p.isolated_margin)
    assert margin <= 0.95 * E
    unlocked = [p.meta["learning"]["learning_unlocked_by"] for p in book.ledger.positions.values()]
    assert [] in unlocked, "baseline'ın da alacağı işlem boş etiket taşır"
    assert any(set(u) & {"TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN", "MAX_POSITION_PCT"} for u in unlocked)
    led = json.loads(book.ledger_path.read_text(encoding="utf-8"))
    assert led["allow_shrink"] is False and book.ledger.allow_shrink is False, "kalıcı öznitelik DEĞİŞMEZ"
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    lrn = summ["learning"]
    assert lrn["active"] is True and lrn["counters"]["opened"] == 12 and lrn["counters"]["learning_unlocked"] >= 1
    assert lrn["risk_profile"] == {"name": "PAPER_RESEARCH", "max_total_open_risk_pct": 100.0, "risk_per_trade_cap_pct": 2.0}
    assert PAPER.max_total_open_risk_pct == 6.0 and book.risk.profile is PAPER, "taban profil ve PROFILES değişmez"


def test_suspension_returns_new_entries_to_the_baseline_and_keeps_positions(tmp_path):
    syms = ["S%02d/USDT" % i for i in range(12)]
    _flag, brk = _trend_rows()
    book = _book(tmp_path, "t2_trend_regime")
    _step(book, _trend_fbs(brk, syms), now_ms=_asof(brk), px=float(brk[-1]["close"]), symbols=syms,
          learning=_bl("t2_trend_regime"))
    assert len(book.ledger.positions) == 12
    more = ["N%02d/USDT" % i for i in range(3)]
    _step(book, _trend_fbs(brk, more), now_ms=_asof(brk) + 900_000, px=float(brk[-1]["close"]), symbols=more, learning=None)
    assert len(book.ledger.positions) == 12 and book.rejections.get("TOTAL_OPEN_RISK") == 3, book.rejections
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    assert summ["learning"]["active"] is False and "suspended_at" in summ["learning"]


def test_min_notional_bump_and_per_call_shrink_backstop(monkeypatch):
    """(a) min-notional'a çıkarma miktarla açılır (adıma YUKARI, %2 tavan içinde); (b) serbest marj yetmezse defter
    YALNIZ bu çağrıda küçültür → size_rule SHRUNK_TO_MARGIN; kalıcı `allow_shrink` false kalır."""
    import tradingbot.strategy_paper as SPM
    f = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.1"),
                      min_qty=Decimal("0.1"), min_notional=Decimal("20"), max_leverage=20)
    led, risk, st, now = _ledger_env()
    lrisk = RiskEngine(profile_for(PAPER), risk.ks)                                  # çağıranın öğrenme RiskEngine'i
    act = {"action": "OPEN", "direction": "LONG", "stop": 90.0, "name": "t2"}          # %10 stop → 1/0,1 = 10 USDT < 20
    res = apply_action(act, symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=lrisk, profile=PAPER, state=st,
                       filters=f, run_id=RUN, reject=lambda s, r: pytest.fail(r), on_closed=lambda r: None, data=_verdict(),
                       learning=_bl("t2_trend_regime"))
    pos = led.positions[SYM]
    assert res == "OPENED" and pos.meta["learning"]["size_rule"] == "BUMP_MIN_NOTIONAL"
    assert pos.qty == Decimal("0.2") and float(pos.qty * pos.entry_avg) >= 20.0
    assert pos.meta["learning"]["risk_usdt"] <= 0.02 * 200.0
    # (b) cüzdan 4 USDT (equity tabanı 200; slot marjı 5): sığmaz; rezerv 0 → defter kendi ücretiyle küçültür. Politika
    # rezervi (öğrenme-ekstra giriş son 2 slotu göremez) ayrı bir kuraldır; yedek emniyeti yalıtmak için burada 0.
    monkeypatch.setattr(SPM, "policy_reserve_usdt", lambda **kw: 0.0)
    led, risk, st, now = _ledger_env(wallet=4.0)
    act = {"action": "OPEN", "direction": "LONG", "stop": 95.0, "name": "t2"}
    fine = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.00001"),
                         min_qty=Decimal("0.00001"), min_notional=Decimal("5"), max_leverage=20)   # yuvarlama ücreti yutmasın
    res = apply_action(act, symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=lrisk, profile=PAPER, state=st,
                       filters=fine, run_id=RUN, reject=lambda s, r: pytest.fail(r), on_closed=lambda r: None, data=_verdict(),
                       learning=_bl("t2_trend_regime", reserve_pct=0.0))
    pos = led.positions[SYM]
    assert res == "OPENED" and pos.meta["learning"]["size_rule"] == SIZE_SHRUNK == pos.features["learning"]["size_rule"]
    assert "shrunk_to_margin" in pos.meta, "defterin çağrı başı küçültmesi devreye girdi"
    assert led.allow_shrink is False and led.to_dict()["allow_shrink"] is False


ETH_F = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                      min_qty=Decimal("0.001"), min_notional=Decimal("20"), max_leverage=20)


@pytest.mark.parametrize("px", [2500.73, 2500.77])
def test_min_notional_bump_survives_risk_engine_4dp_rounding(px):
    """RiskEngine `adjusted_notional`ı 4 haneye yuvarlar (20.01224 → 20.0122); çıkarma bu artık yüzünden NOTIONAL'a düşüp
    defterde bir adım kaybetmemeli (0.007 × 2501 = 17.5 < 20 → MIN_NOTIONAL). Beş ondalıklı qty × dolum (ETH tick/step)."""
    led, risk, st, now = _ledger_env()
    lrisk = RiskEngine(profile_for(PAPER), risk.ks)
    tick = TickData(last=Decimal(str(px)), mark=Decimal(str(px)), ts=now.isoformat())
    act = {"action": "OPEN", "direction": "LONG", "stop": px * 0.90, "targets": [px * 1.2], "name": "t2", "leverage": 2,
           "leverage_max": 4}
    rej: list = []
    res = apply_action(act, symbol=SYM, price=px, tick=tick, now=now, ledger=led, risk=lrisk, profile=PAPER, state=st,
                       filters=ETH_F, run_id=RUN, reject=lambda s, r: rej.append(r), on_closed=lambda r: None,
                       data=_verdict(), learning=_bl("t2_trend_regime"))
    fit = act["_learning"]["fit"]
    assert fit["ok"] and fit["size_rule"] == "BUMP_MIN_NOTIONAL" and round(fit["notional"], 4) != fit["notional"], fit
    assert res == "OPENED", (rej, fit)
    pos = led.positions[SYM]
    assert pos.qty == Decimal("0.008") and float(pos.qty * pos.entry_avg) >= 20.0
    assert pos.meta["learning"]["size_rule"] == "BUMP_MIN_NOTIONAL" and pos.meta["learning"]["risk_usdt"] <= 0.02 * 200.0


@pytest.mark.parametrize("px,tick", [(100.0, "0.01"), (1.0, "0.001"), (0.0123, "0.0001")])
def test_learning_sizes_on_the_fill_price_and_tags_the_realised_risk(px, tick):
    """Box (öğrenme min_stop %0,32): stop mesafesi defterin DOLUM fiyatından ölçülür (kayma + tick yukarı); etiketlenen
    `risk_usdt` / `risk_fraction_of_budget` gerçekleşen pozisyonun riskine eşit ve %0,5 bütçenin içinde (eskiden mark'tan
    ölçülüyordu: kaba tick'te gerçek risk etiketin 3,5 katıydı)."""
    led, risk, st, now = _ledger_env()
    lrisk = RiskEngine(profile_for(PAPER), risk.ks)
    f = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal(tick), qty_step=Decimal("0.001"),
                      min_qty=Decimal("0.001"), min_notional=Decimal("5"), max_leverage=20)
    stop = px * (1 - 0.0032)
    tk = TickData(last=Decimal(str(px)), mark=Decimal(str(px)), ts=now.isoformat())
    act = {"action": "OPEN", "direction": "LONG", "stop": stop, "targets": [px * 1.01], "name": "box", "leverage": 3,
           "leverage_max": 4}
    res = apply_action(act, symbol=SYM, price=px, tick=tk, now=now, ledger=led, risk=lrisk, profile=PAPER, state=st,
                       filters=f, run_id=RUN, reject=lambda s, r: pytest.fail(r), on_closed=lambda r: None,
                       data=_verdict(), learning=_bl("b1_box_fade"))
    assert res == "OPENED"
    pos = led.positions[SYM]
    lr = pos.meta["learning"]
    actual = float(pos.qty) * abs(float(pos.entry_avg) - stop)
    assert lr["risk_usdt"] == pytest.approx(actual, rel=1e-5, abs=1e-6) and lr == pos.features["learning"]
    assert actual <= 0.005 * 200.0 + 1e-9 and lr["risk_fraction_of_budget"] == pytest.approx(actual / 1.0, rel=1e-5)
    assert float(pos.entry_avg) > px                                      # dolum mark'ın üstünde (LONG, kayma)


def test_book_learning_risk_engine_honours_max_total_open_risk_pct():
    """`learning_mode.max_total_open_risk_pct` defterlerde de geçerli (ana bot ve Formasyon gibi); eskiden hep 100'dü."""
    sec = _section(t2_trend_regime={"enabled": True, "slots": 40})
    sec.max_total_open_risk_pct = 50.0
    lm = LearningMode(sec, mode_gate=lambda: (True, "OK"))
    lm.refresh()
    bl = lm.book("t2_trend_regime")
    assert bl.max_total_open_risk_pct == 50.0 == lm.profile_for(PAPER).max_total_open_risk_pct
    fake = types.SimpleNamespace(risk_learning=None, _risk_learning_key=None, profile=PAPER,
                                 risk=types.SimpleNamespace(ks=KillSwitch(), clusters=None))
    assert StrategyBook._learning_risk(fake, bl).profile.max_total_open_risk_pct == 50.0
    assert PAPER.max_total_open_risk_pct == 6.0
    assert _bl("t2_trend_regime").max_total_open_risk_pct == 100.0          # varsayılan (config L1 değeri) değişmez


# ============================================================================ 2b) taban görünümü ve politika rezervi
@pytest.mark.parametrize("name", ["t2_trend_regime", "d4_donchian_20_10"])
def test_policy_label_matches_what_the_baseline_book_actually_opens(name, tmp_path):
    """(2026-09-28, ikinci doğrulama turu) `learning_unlocked_by` boş (= "taban da açardı") etiketi taban kapılarını
    öğrenme defterinin küçük pozisyonlarıyla DEĞİL taban görünümüyle (öğrenme-ekstra yok, politika pozisyonları taban
    boyutunda) ölçer: aynı sinyallerde politika etiketli işlemler taban defterin GERÇEKTEN açtıklarıdır. Eskiden öğrenme
    defterinin boş marjı/riski yüzünden taban TOTAL_OPEN_RISK / INSUFFICIENT_MARGIN ile reddettiği işlemler de "politika"
    sayılıyordu (karne sütunu şişiyordu)."""
    syms = ["S%02d/USDT" % i for i in range(12)]
    if name == "t2_trend_regime":
        _flag, brk = _trend_rows()
        fbs, now_ms, px = _trend_fbs(brk, syms), _asof(brk), float(brk[-1]["close"])
    else:
        fbs, now_ms, px = _d4_fbs(syms), NOW_H4, 104.1
    uni = syms if name == "d4_donchian_20_10" else None
    base = _book(tmp_path / "base", name, symbols=uni)
    _step(base, fbs, now_ms=now_ms, px=px, symbols=syms)
    book = _book(tmp_path / "learn", name, symbols=uni)
    _step(book, fbs, now_ms=now_ms, px=px, symbols=syms, learning=_bl(name))
    assert set(book.ledger.positions) == set(syms)
    policy = {s for s, p in book.ledger.positions.items() if not p.meta["learning"]["learning_unlocked_by"]}
    assert policy == set(base.ledger.positions), (sorted(policy), sorted(base.ledger.positions), base.rejections)
    for s in policy:                                     # taban görünümü politika pozisyonunu taban boyutunda sayar
        bs = book.ledger.positions[s].meta["learning"]["baseline_size"]
        bp = base.ledger.positions[s]
        assert bs["leverage"] == bp.leverage and bs["notional"] == pytest.approx(float(bp.qty * bp.entry_avg), rel=0.02)
    extra = [p.meta["learning"]["learning_unlocked_by"] for s, p in book.ledger.positions.items() if s not in policy]
    assert extra and all(set(u) & set(base.rejections) for u in extra), (extra, base.rejections)


def test_policy_reserve_keeps_margin_for_signals_the_baseline_would_take():
    """Politika rezervi (2026-09-28, ikinci doğrulama turu): öğrenme-ekstra giriş serbest marjın son 2 slotunu GÖREMEZ
    (INSUFFICIENT_MARGIN → karşı-olgusal), taban kuralların da alacağı sinyal aynı durumda AÇILIR. Eskiden keşif işlemleri
    defteri %95'e doldurunca politika işlemi (ana bot UNI, Box sinyalleri) dışarıda kalıyordu."""
    from tradingbot.learning_mode import policy_reserve_usdt
    lrisk = RiskEngine(profile_for(PAPER), KillSwitch())
    f5 = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                       min_qty=Decimal("0.001"), min_notional=Decimal("5"), max_leverage=20)
    bl = _bl("d4_donchian_20_10")                                  # K=20, E=200 → slot marjı 9,5; rezerv 19
    assert policy_reserve_usdt(equity=200.0, slots=20, reserve_pct=5.0) == pytest.approx(19.0)
    out = {}
    for kind, unlocked in (("policy", []), ("extra", ["BOOK_UNIVERSE"])):
        led, risk, st, now = _ledger_env(wallet=25.0)              # serbest 25: genel rezerv 10 + 1 slot sığar, +19 sığmaz
        act = {"action": "OPEN", "direction": "LONG", "stop": 95.0, "targets": [], "name": "d4", "leverage": 1,
               "_learning": {"unlocked_by": list(unlocked)}}
        rej: list = []
        res = apply_action(act, symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=lrisk, profile=PAPER,
                           state=st, filters=f5, run_id=RUN, reject=lambda s, r: rej.append(r), on_closed=lambda r: None,
                           data=_verdict(), learning=bl)
        out[kind] = (res, rej, act["_learning"]["fit"], led)
    res, rej, fit, led = out["policy"]
    assert res == "OPENED" and rej == [] and fit["policy_grade"] is True and fit["policy_reserve_usdt"] == 0.0
    lr = led.positions[SYM].meta["learning"]
    assert lr["learning_unlocked_by"] == [] and lr["size_rule"] == SIZE_SLOT and lr["baseline_size"]["notional"] > 0
    res, rej, fit, led = out["extra"]
    assert res == "REJECTED" and rej == ["INSUFFICIENT_MARGIN"] and not led.positions
    assert fit["policy_grade"] is False and fit["policy_reserve_usdt"] == pytest.approx(19.0)


def test_box_signal_on_a_held_symbol_becomes_a_position_open_counterfactual(tmp_path):
    """SPEC A15 "+CF" (2026-09-28, ikinci doğrulama turu): dar stoplu öğrenme işlemi sembolü tutarken gelen (tabanın
    AÇTIĞI) geniş stoplu Box sinyali kaybolmaz → POSITION_OPEN karşı-olgusalı. Açık pozisyonun kendi sinyali yazılmaz."""
    base = _book(tmp_path / "base", "b1_box_fade", mode="SHADOW")
    _step(base, _box_fbs(BOX_TIGHT), now_ms=NOW_M5, px=108.3)
    assert SYM not in base.ledger.positions
    _step(base, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + M5), now_ms=NOW_M5 + M5, px=107.0)
    assert SYM in base.ledger.positions, "önkoşul: taban bu sinyali alır"
    book = _book(tmp_path / "learn", "b1_box_fade", mode="SHADOW")
    lrn = _bl("b1_box_fade")
    _step(book, _box_fbs(BOX_TIGHT), now_ms=NOW_M5, px=108.3, learning=lrn)
    assert "BOX_MIN_STOP_PCT" in book.ledger.positions[SYM].meta["learning"]["learning_unlocked_by"]
    _step(book, _box_fbs(BOX_TIGHT), now_ms=NOW_M5 + 60_000, px=108.3, learning=lrn)   # aynı bar: kendi sinyali
    assert book.cf.sb.trades == []
    # yeni barda yalnız öğrenme parametresiyle (0,32) doğan dar stoplu sinyal: taban almazdı → açık sembolde kayıt YOK
    _step(book, _box_fbs(BOX_TIGHT, now_ms=NOW_M5 + M5), now_ms=NOW_M5 + M5, px=108.3, learning=lrn)
    assert book.cf.sb.trades == []
    _step(book, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + 2 * M5), now_ms=NOW_M5 + 2 * M5, px=107.0, learning=lrn)
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == ["POSITION_OPEN"] and t.direction == "SHORT" and t.stop == 110.0
    assert t.label_kind == "TARGET_STOP_TIME" and t.tf_minutes == 5 and t.targets == [90.0]
    assert float(book.ledger.positions[SYM].stop) == pytest.approx(109.6), "sembol başına tek pozisyon kalır"


def test_held_symbol_counterfactual_counts_one_move_once(tmp_path):
    """(2026-09-28, üçüncü doğrulama turu; bulgu R1-3) A15 "+CF" aynı hareketin her taze taban sinyalini POSITION_OPEN
    olarak yazıyordu (uçtan uca koşu: DOGE 08:15–09:30 altı kayıt, hepsi aynı stop/hedef/etiket anı; 25 kaydın 13'ü taban
    kendi pozisyonunu tutarken). Şimdi: (a) tabanın varsayımsal pozisyonu (sonuçlanmamış POSITION_OPEN kaydı) açıkken yeni
    sinyal yazılmaz, sonuçlanınca yazılır; (b) tutulan pozisyon politika (taban da tutuyor) ise hiç yazılmaz."""
    lrn = _bl("b1_box_fade")
    book = _book(tmp_path / "extra", "b1_box_fade", mode="SHADOW")
    _step(book, _box_fbs(BOX_TIGHT), now_ms=NOW_M5, px=108.3, learning=lrn)
    assert "BOX_MIN_STOP_PCT" in book.ledger.positions[SYM].meta["learning"]["learning_unlocked_by"]
    _step(book, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + 2 * M5), now_ms=NOW_M5 + 2 * M5, px=107.0, learning=lrn)
    (first,) = book.cf.sb.trades
    assert first.reason_not_opened == ["POSITION_OPEN"] and first.outcome is None
    assert first.features["baseline_blocked_by"] == [], "taban kapıları geçer: varsayımsal taban pozisyonu"
    _step(book, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + 3 * M5), now_ms=NOW_M5 + 3 * M5, px=107.0, learning=lrn)
    assert book.cf.sb.trades == [first], "tabanın varsayımsal pozisyonu açık: aynı hareketin yeni sinyali yazılmaz"
    first.outcome = {"exit_reason": "stop", "r_multiple": -1.0}         # varsayımsal pozisyon sonuçlandı
    _step(book, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + 4 * M5), now_ms=NOW_M5 + 4 * M5, px=107.0, learning=lrn)
    assert len(book.cf.sb.trades) == 2 and book.cf.sb.trades[1].reason_not_opened == ["POSITION_OPEN"]
    assert book.cf.sb.trades[1].signal_key != first.signal_key
    # (b) tutulan pozisyon POLİTİKA: taban da aynı pozisyonu tutuyor → taze sinyal kaydı yok
    pol = _book(tmp_path / "policy", "b1_box_fade", mode="SHADOW")
    _step(pol, _box_fbs(BOX_TIGHT), now_ms=NOW_M5, px=108.3, learning=lrn)
    pol.ledger.positions[SYM].meta["learning"]["learning_unlocked_by"] = []
    for k in (2, 3):
        _step(pol, _box_fbs(BOX_WIDE, now_ms=NOW_M5 + k * M5), now_ms=NOW_M5 + k * M5, px=107.0, learning=lrn)
    assert pol.cf.sb.trades == []


def test_held_symbol_counterfactual_uses_a_virtual_baseline_portfolio(tmp_path):
    """(2026-09-28, üçüncü doğrulama turu) Uçtan uca koşu: yalnız "sonuçlanmamış kayıt = taban pozisyonu" kuralı, tabanın
    TOTAL_OPEN_RISK ile ALMADIĞI önceki sinyali varsayımsal pozisyon sayıp tabanın sonraki GERÇEK girişini bastırıyordu
    (OP 10:35 kaydı → OFF'un OP 11:15 işlemi kayıtsız). Taban kapıları sanal taban portföyüyle (taban görünümü +
    sonuçlanmamış varsayımsal pozisyonlar taban boyutunda) ölçülür: durduracaksa kayıt `baseline_blocked_by` taşır ve
    varsayımsal pozisyon SAYILMAZ."""
    import dataclasses
    s2 = "SOL/USDT"
    syms = (SYM, s2)
    lrn = _bl("b1_box_fade")
    book = _book(tmp_path, "b1_box_fade", mode="SHADOW")
    rp = float(book.profile.risk_per_trade_pct)
    book.profile = dataclasses.replace(book.profile, max_total_open_risk_pct=1.5 * rp)     # tek taban pozisyonu sığar
    _step(book, _box_fbs(BOX_TIGHT, syms=syms), now_ms=NOW_M5, px=108.3, symbols=list(syms), learning=lrn)
    assert all(book.ledger.positions[s].meta["learning"]["learning_unlocked_by"] for s in syms), "ikisi de ekstra"
    t2 = NOW_M5 + 2 * M5
    _step(book, _box_fbs(BOX_WIDE, now_ms=t2, syms=syms), now_ms=t2, px=107.0, symbols=list(syms), learning=lrn)
    a, b = book.cf.sb.trades
    assert (a.symbol, a.features["baseline_blocked_by"]) == (SYM, [])
    assert b.symbol == s2 and "TOTAL_OPEN_RISK" in b.features["baseline_blocked_by"], b.features
    # aynı hareketin yeni sinyalleri: SYM (varsayımsal pozisyon açık) ve s2 (kapıya takılan kaydı sonuçlanmadı) yazılmaz
    t3 = NOW_M5 + 3 * M5
    _step(book, _box_fbs(BOX_WIDE, now_ms=t3, syms=syms), now_ms=t3, px=107.0, symbols=list(syms), learning=lrn)
    assert book.cf.sb.trades == [a, b]
    # tabanın varsayımsal SYM pozisyonu sonuçlandı → bütçe serbest: s2'nin sinyali (sonuçlanmamış ENGELLİ kaydı olsa da)
    # taban girişi olur — engelli kayıt varsayımsal pozisyon sayılmaz, tabanın gerçek girişini BASTIRMAZ; SYM bu kez kapıya takılır
    a.outcome = {"exit_reason": "stop", "r_multiple": -1.0}
    t4 = NOW_M5 + 4 * M5
    _step(book, _box_fbs(BOX_WIDE, now_ms=t4, syms=syms), now_ms=t4, px=107.0, symbols=[s2, SYM], learning=lrn)
    c, d = book.cf.sb.trades[2:]
    assert (c.symbol, c.features["baseline_blocked_by"]) == (s2, [])
    assert d.symbol == SYM and "TOTAL_OPEN_RISK" in d.features["baseline_blocked_by"]


# ============================================================================ 3) ölçüm kapıları AYNEN durdurur
def test_keeps_still_block_under_learning(tmp_path):
    lrn = _bl("d4_donchian_20_10")
    # DATA_*: provenans yok → giriş yok, karşı-olgusal YOK
    book = _book(tmp_path / "data", "d4_donchian_20_10", symbols=[SYM])
    _step(book, _d4_fbs(), now_ms=NOW_H4, px=104.1, prov={}, learning=lrn)
    assert not book.ledger.positions and book.rejections == {"DATA_PROVENANCE_MISSING": 1}
    assert book.cf.stats()["recorded_total"] == 0
    # BAD_STOP: fiyat stop'un altında (R tanımsız) → karşı-olgusal YOK
    book = _book(tmp_path / "stop", "d4_donchian_20_10", symbols=[SYM])
    _step(book, _d4_fbs(), now_ms=NOW_H4, px=90.0, learning=lrn)
    assert not book.ledger.positions and book.rejections == {"STRATEGY_BAD_STOP": 1}
    assert book.cf.stats()["recorded_total"] == 0
    # RISK_OUTSIDE_TESTED_RANGE (laboratuvar paritesi) → reddedilir, karşı-olgusal VAR (HORIZON, yaklaşık)
    book = _book(tmp_path / "range", "d4_donchian_20_10", symbols=[SYM])
    fbs = _d4_fbs()
    act = paper_rules.decide_for("d4_donchian_20_10", frames=fbs[SYM], btc_rows=None, now_ms=NOW_H4, params=book.rule_params)
    near = act["stop"] + 0.05 * act["atr14"]
    _step(book, fbs, now_ms=NOW_H4, px=near, learning=lrn)
    assert not book.ledger.positions and book.rejections == {"RISK_OUTSIDE_TESTED_RANGE": 1}
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == ["RISK_OUTSIDE_TESTED_RANGE"] and t.label_kind == "HORIZON" and t.approx is True
    assert t.signal_key == t.plan_id == "signal_ts:%d" % act["signal_ts"] and t.tf_minutes == 240
    # SIGNAL_ALREADY_USED: aynı sinyalle ikinci giriş yok (stop aynı barda geldi)
    book = _book(tmp_path / "used", "d4_donchian_20_10", symbols=[SYM])
    _step(book, fbs, now_ms=NOW_H4, px=104.1, learning=lrn)
    pos = book.ledger.positions[SYM]
    book.ledger.close_manual(SYM, float(pos.stop), reason="STOP", now=_dt(NOW_H4 + 300_000))
    _step(book, fbs, now_ms=NOW_H4 + 900_000, px=104.0, learning=lrn)
    assert SYM not in book.ledger.positions and book.last_actions[SYM]["reason"] == "SIGNAL_ALREADY_USED"
    assert book.cf.stats()["recorded_total"] == 0
    # sembol başına tek pozisyon: açık pozisyon varken OPEN uygulanmaz
    led, risk, st, now = _ledger_env()
    a = {"action": "OPEN", "direction": "LONG", "stop": 95.0, "name": "t2"}
    assert apply_action(dict(a), symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=risk, profile=PAPER, state=st,
                        filters=None, run_id=RUN, reject=lambda s, r: None, on_closed=lambda r: None, data=_verdict(),
                        learning=_bl("t2_trend_regime")) == "OPENED"
    assert apply_action(dict(a), symbol=SYM, price=100.0, tick=None, now=now, ledger=led, risk=risk, profile=PAPER, state=st,
                        filters=None, run_id=RUN, reject=lambda s, r: None, on_closed=lambda r: None, data=_verdict(),
                        learning=_bl("t2_trend_regime")) == "NONE"
    assert len(led.positions) == 1


# ============================================================================ 4) karşı-olgusal kayıt
def test_one_blocked_signal_over_sixteen_tours_is_one_counterfactual_and_is_labelled_later(tmp_path):
    """P1: anahtar barın sinyal kimliği (tur kimliği DEĞİL) → aynı günlük bar 16 tur boyunca bloke kalsa da TEK kayıt.
    Etiket sonra, eldeki çerçeveyle (ek API yok): stop ÖNCE."""
    big = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                        min_qty=Decimal("0.001"), min_notional=Decimal("500"), max_leverage=20)
    _flag, brk = _trend_rows()
    book = _book(tmp_path, "t2_trend_regime", filters=[big])
    px = float(brk[-1]["close"])
    for k in range(16):
        book.run_id = "RUN-%d" % k                        # tur kimliği her turda değişir
        _step(book, _trend_fbs(brk), now_ms=_asof(brk) + k * 900_000, px=px, learning=_bl("t2_trend_regime"))
    assert book.rejections == {"MIN_ORDER_CONFLICT": 16} and not book.ledger.positions
    assert float(book.ledger.wallet_balance) == 200.0, "karşı-olgusal deftere/özkaynağa DOKUNMAZ"
    (t,) = book.cf.sb.trades
    assert t.plan_id == "signal_ts:%d" % int(brk[-1]["timestamp"]) and "RUN" not in t.plan_id
    assert t.book == "t2_trend_regime" and t.reason_not_opened == ["MIN_ORDER_CONFLICT"] and t.is_counterfactual
    assert t.entry == pytest.approx(px) and t.stop < px and t.targets == [] and t.label_kind == "HORIZON"
    disk = json.loads((book.state_dir / CF_FILE).read_text(encoding="utf-8"))
    assert len(disk["trades"]) == 1 and disk["meta"]["recorded_total"] == 1
    # iki gün sonra: ertesi günün barı giriş günüdür (etiket girişten SONRAKİ ilk bardan başlar), ardından stop'u delen bar
    b1 = dict(brk[-1], timestamp=int(brk[-1]["timestamp"]) + DAY)
    b2 = dict(brk[-1], timestamp=int(brk[-1]["timestamp"]) + 2 * DAY, low=t.stop * 0.95, close=t.stop * 0.97)
    rows = list(brk) + [b1, b2]
    _step(book, _trend_fbs(rows), now_ms=_asof(rows), px=t.stop * 0.97, learning=_bl("t2_trend_regime"))
    t = next(x for x in book.cf.sb.trades if x.plan_id == t.plan_id)
    assert t.outcome and t.outcome["exit_reason"] == "stop" and t.outcome["r_multiple"] == pytest.approx(-1.0)
    assert t.outcome["label_kind"] == "HORIZON" and t.outcome["approx"] is True
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    assert summ["learning"]["counterfactual"]["labeled"] >= 1 and summ["learning"]["counters"]["counterfactual_labeled"] >= 1


def test_counterfactual_is_superseded_when_the_same_signal_opens_on_a_later_tour(tmp_path):
    """D4: tur 1 kill switch → karşı-olgusal; 15 dk sonra (aynı 4h sinyali, 60 dk penceresi içinde) açılır → kayıt düşer
    (`superseded`); aynı gözlem hem dolum hem "açılmadı" olarak SAYILMAZ, sonraki turda yeniden de yazılmaz."""
    lrn = _bl("d4_donchian_20_10")
    book = _book(tmp_path, "d4_donchian_20_10", symbols=[SYM])
    fbs = _d4_fbs()
    book.risk.ks.trip("TEST", "manual trip")
    _step(book, fbs, now_ms=NOW_H4, px=104.1, learning=lrn)
    assert SYM not in book.ledger.positions and book.rejections == {"KILL_SWITCH_ACTIVE": 1}
    assert [t.reason_not_opened for t in book.cf.sb.trades] == [["KILL_SWITCH_ACTIVE"]]
    book.risk.ks.reset("test", "reset")
    _step(book, fbs, now_ms=NOW_H4 + 15 * 60_000, px=104.1, learning=lrn)
    assert SYM in book.ledger.positions
    assert book.cf.sb.trades == [] and book.cf.stats()["superseded"] == 1
    assert book.learning_counters["counterfactual_superseded"] == 1
    doc = json.loads((book.state_dir / CF_FILE).read_text(encoding="utf-8"))
    assert doc["trades"] == [] and doc["meta"]["superseded"] == 1


def test_trade_memory_entry_rows_carry_learning_tags_only_under_learning(tmp_path):
    """İşlem hafızası (öğrenen katman) satırı da `learning` etiketlerini taşır; öğrenmesiz açılışta satır bugünküyle aynı."""
    out = {}
    for name, kw in (("off", {}), ("on", {"learning": _bl("d4_donchian_20_10")})):
        book = _book(tmp_path / name, "d4_donchian_20_10", symbols=[SYM])
        _step(book, _d4_fbs(), now_ms=NOW_H4, px=104.1, **kw)
        assert SYM in book.ledger.positions, name
        rows = [r for r in book.memory.iter_rows() if r.get("kind") == "entry"]
        assert len(rows) == 1
        out[name] = rows[0]["features"]
    assert "learning" not in out["off"] and "in_lab_universe" not in out["off"]
    pos_tags = out["on"]["learning"]
    assert pos_tags["size_rule"] == SIZE_SLOT and pos_tags["book"] == "d4_donchian_20_10" and out["on"]["in_lab_universe"] is True
    assert {k: v for k, v in out["on"].items() if k not in ("learning", "in_lab_universe")}.keys() == out["off"].keys()


# ============================================================================ 5) T2/M2 yapı GİRİŞİ gölgesi, yönetim ENFORCE
def test_t2_structure_entry_shadow_opens_with_the_rules_own_geometry_and_tags_the_unlock(tmp_path):
    flag, _brk = _trend_rows()
    fbs, now_ms, px = _trend_fbs(flag), _asof(flag), float(flag[-1]["close"])
    # baseline ENFORCE: bayrak oluşuyor → tetiği bekle
    base = _book(tmp_path / "base", "t2_trend_regime", mode="ENFORCE")
    _step(base, fbs, now_ms=now_ms, px=px, structures_entry_shadow=True)            # öğrenme yokken ezme YOK SAYILIR
    assert SYM not in base.ledger.positions and base.last_actions[SYM]["reason"] == "STRUCTURE_WAIT_TRIGGER"
    # öğrenme + giriş gölgesi: kuralın KENDİ girişi ve stop'u
    book = _book(tmp_path / "learn", "t2_trend_regime", mode="ENFORCE")
    _step(book, fbs, now_ms=now_ms, px=px, learning=_bl("t2_trend_regime"), structures_entry_shadow=True)
    pos = book.ledger.positions[SYM]
    assert pos.features["structure"]["shadow"] is True and pos.features["structure"]["action"] == "WAIT_TRIGGER"
    assert pos.meta["learning"]["learning_unlocked_by"][0] == "STRUCTURE_WAIT_TRIGGER"
    assert book.structure_decisions[SYM]["mode"] == "SHADOW" and book.structure_decisions[SYM]["applied"] == "OPENED"
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    assert summ["learning"]["structures_entry_shadow"] is True and summ["structures"]["mode"] == "ENFORCE"
    # öğrenme açık ama bot ezme listesinde değil: yapı durdurur → kuralın geometrisiyle karşı-olgusal
    book = _book(tmp_path / "noshadow", "t2_trend_regime", mode="ENFORCE")
    _step(book, fbs, now_ms=now_ms, px=px, learning=_bl("t2_trend_regime"))
    assert SYM not in book.ledger.positions
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == ["STRUCTURE_WAIT_TRIGGER"] and t.features["structure"]["action"] == "WAIT_TRIGGER"
    assert t.stop == pytest.approx(float(pos.stop)), "karşı-olgusal kuralın KENDİ stop'unu taşır"


def test_m2_entry_shadow_keeps_structure_management_enforced(tmp_path):
    """Giriş gölgede açılır; açık pozisyonda yapı yönetimi ENFORCE kalır: girişten sonra teyitli süpürme → M2 yapı çıkışı."""
    flag, brk = _trend_rows()
    after = swing_high_then_sweep(brk, step=DAY)
    book = _book(tmp_path, "m2_tsmom28", mode="ENFORCE")
    lrn = _bl("m2_tsmom28")
    _step(book, _trend_fbs(flag), now_ms=_asof(flag), px=float(flag[-1]["close"]), learning=lrn, structures_entry_shadow=True)
    assert SYM in book.ledger.positions
    _step(book, _trend_fbs(brk), now_ms=_asof(brk), px=float(brk[-1]["close"]), learning=lrn, structures_entry_shadow=True)
    assert book.structure_decisions[SYM]["mode"] == "ENFORCE", "açık pozisyonun yönetimi gölgeye ALINMAZ"
    _step(book, _trend_fbs(after), now_ms=_asof(after), px=float(after[-1]["close"]), learning=lrn, structures_entry_shadow=True)
    assert SYM not in book.ledger.positions and book.ledger.history[-1].exit_reason == "M2_STRUCTURE_EXIT"
    assert book.structure_decisions[SYM]["action"] == "EXIT" and book.structure_decisions[SYM]["mode"] == "ENFORCE"


def test_m2_entry_shadow_keeps_entry_structure_failed_exit_for_an_approved_entry(tmp_path):
    """Yapının ONAYLADIĞI (ENTER) M2 girişi giriş gölgesinde de gölge işaretsiz referans taşır: bayrak bozulunca baseline
    ENFORCE gibi ENTRY_STRUCTURE_FAILED ile kapanır (eskiden `shadow=True` yüzünden giriş kimliği yok sayılıyordu)."""
    from tradingbot.structures.bots import _entry_pid, used_patterns_of
    flag, brk = _trend_rows()
    inv = min(r["low"] for r in flag[-5:])
    lb = brk[-1]
    fail = list(brk) + [{"timestamp": lb["timestamp"] + DAY, "open": lb["close"] * 0.998, "high": lb["close"] * 0.999,
                         "low": inv * 0.985, "close": inv * 0.99, "volume": 1.0}]
    out = {}
    for name, kw in (("base", {}), ("learn", {"learning": _bl("m2_tsmom28"), "structures_entry_shadow": True})):
        book = _book(tmp_path / name, "m2_tsmom28", mode="ENFORCE")
        _step(book, _trend_fbs(brk), now_ms=_asof(brk), px=float(brk[-1]["close"]), **kw)
        pos = book.ledger.positions[SYM]
        assert pos.features["structure"]["action"] == "ENTER" and pos.features["structure"].get("shadow") is None, name
        assert book.structure_decisions[SYM]["mode"] == "ENFORCE", name
        pid = _entry_pid(pos)
        assert pid and set(used_patterns_of(book.ledger)) == {pid}
        _step(book, _trend_fbs(fail), now_ms=_asof(fail), px=float(fail[-1]["close"]), **kw)
        assert SYM not in book.ledger.positions, name
        out[name] = (pid, book.ledger.history[-1].exit_reason)
    assert out["base"] == out["learn"] and out["learn"][1] == "ENTRY_STRUCTURE_FAILED"


# ============================================================================ 6) Box öğrenme min_stop
def test_box_learning_uses_a_second_params_object_with_min_stop_032(tmp_path):
    fbs = _box_fbs(BOX_TIGHT)
    base = _book(tmp_path / "base", "b1_box_fade", mode="SHADOW")
    _step(base, fbs, now_ms=NOW_M5, px=108.3)
    assert SYM not in base.ledger.positions and base.last_actions[SYM]["reason"] == "NO_SIGNAL", "%1,2 stop < 2,22 taban eşiği"
    book = _book(tmp_path / "learn", "b1_box_fade", mode="SHADOW")
    _step(book, fbs, now_ms=NOW_M5, px=108.3, learning=_bl("b1_box_fade"))
    pos = book.ledger.positions[SYM]
    assert pos.side.value == "SHORT" and float(pos.stop) == pytest.approx(109.6)
    assert "BOX_MIN_STOP_PCT" in pos.meta["learning"]["learning_unlocked_by"]
    assert book.rule_params.min_stop_pct == 2.22 and book.rule_params_learning.min_stop_pct == 0.32
    summ = json.loads((Path(book.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))
    assert summ["rule_params"]["min_stop_pct"] == 2.22 and summ["learning"]["rule_params_effective"] == {"min_stop_pct": 0.32}
    # askıya alınınca aynı çerçeve taban eşiğiyle değerlendirilir
    other = _box_fbs(BOX_TIGHT, syms=("SOL/USDT",))
    _step(book, other, now_ms=NOW_M5 + 60_000, px=108.3, learning=None)
    assert "SOL/USDT" not in book.ledger.positions


def test_box_counterfactual_labels_to_the_end_of_the_signal_day(tmp_path):
    """Box karşı-olgusalı TARGET_STOP_TIME: kuralın kendi hedefi/stop'u ve gün sonu düzleşmesi (5m ufuk)."""
    big = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                        min_qty=Decimal("0.001"), min_notional=Decimal("5000"), max_leverage=20)
    book = _book(tmp_path, "b1_box_fade", mode="SHADOW", filters=[big])
    _step(book, _box_fbs(BOX_WIDE), now_ms=NOW_M5, px=107.0, learning=_bl("b1_box_fade"))
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == ["MIN_ORDER_CONFLICT"] and t.label_kind == "TARGET_STOP_TIME" and t.tf_minutes == 5
    assert t.targets == [90.0] and t.stop == 110.0 and t.direction == "SHORT" and t.approx is False
    eod = (NOW_M5 // DAY) * DAY + DAY
    first = (NOW_M5 // M5 + 1) * M5
    assert t.horizon_bars == (eod - first) // M5
    assert datetime.fromisoformat(t.label_ts) < _dt(eod) and datetime.fromisoformat(t.label_ts) >= _dt(eod - M5)


# ============================================================================ 7) C4: also_matched ve açık pozisyon
def test_c4_also_matched_and_position_blocked_variations_become_counterfactuals(tmp_path, registry, monkeypatch):
    ids = registry(VX, VY)
    book = _c4_book(tmp_path, ids)
    fbs = _c4_fbs()
    lrn = _bl("c4_candle_variations")
    hits = paper_rules.candle_hits_for("c4_candle_variations", frames=fbs[SYM], now_ms=NOW_H4, params=book.rule_params)
    assert [h["id"] for h in hits] == ids
    _step(book, fbs, now_ms=NOW_H4, px=96.6, learning=lrn)
    pos = book.ledger.positions[SYM]
    assert pos.features["candle_variation"]["id"] == ids[0] and pos.features["in_lab_universe"] is True
    (t,) = book.cf.sb.trades
    h = hits[1]
    assert t.variation == ids[1] and t.reason_not_opened[0] in ("ALSO_MATCHED", "RISK_OUTSIDE_TESTED_RANGE")
    assert t.label_kind == "TARGET_STOP_TIME" and t.horizon_bars == max(1, h["max_hold_bars"] - 1)
    assert t.stop == pytest.approx(h["stop"]) and t.rule_version == h["definition_sha"]
    risk = abs(96.6 - h["stop"])
    assert t.targets == [pytest.approx(96.6 + (1 if h["direction"] == "LONG" else -1) * h["target_r"] * risk)]
    # aynı bar, pozisyon BU sinyalden açık → yeni kayıt yok (eşleşenler açılışta kaydedildi)
    _step(book, fbs, now_ms=NOW_H4 + 900_000, px=96.7, learning=lrn)
    assert book.cf.stats()["recorded_total"] == 1
    # sonraki bir sinyal açık pozisyon yüzünden girilemez → varyasyon başına POSITION_OPEN
    later = [dict(x, signal_ts=x["signal_ts"] + H4, signal_close_ms=x["signal_close_ms"] + H4) for x in hits]
    monkeypatch.setattr(paper_rules, "candle_hits_for", lambda *a, **k: [dict(x) for x in later])
    _step(book, fbs, now_ms=NOW_H4 + 1_800_000, px=96.7, learning=lrn)
    new = [x for x in book.cf.sb.trades if x.reason_not_opened == ["POSITION_OPEN"]]
    assert sorted(x.variation for x in new) == sorted(ids) and book.cf.stats()["recorded_total"] == 3
    assert list(book.ledger.positions) == [SYM], "sembol başına tek pozisyon kalır"


def test_candle_entry_hits_match_decide(registry):
    ids = registry(VX, VY)
    rows = _rows_at(EXAMPLES[CV0]["match"], NOW_H4)
    at = int(rows[-1]["timestamp"]) + H4 + 60_000
    for order in (ids, ids[::-1]):
        p = paper_rules.build_params("c4_candle_variations", rule_params={"variations": list(order)})
        act = candle_book.decide("c4_candle_variations", rows=rows, now_ms=at, params=p)
        hits = candle_book.entry_hits("c4_candle_variations", rows=rows, now_ms=at, params=p)
        assert [h["id"] for h in hits] == [act["lab_algo"]] + act["also_matched"]
        assert hits[0]["stop"] == act["stop"] and hits[0]["signal_ts"] == act["signal_ts"]
        assert hits[0]["max_hold_bars"] == act["variation"]["max_hold_bars"]
        late = int(rows[-1]["timestamp"]) + H4 + 61 * 60_000
        assert candle_book.entry_hits("c4_candle_variations", rows=rows, now_ms=late, params=p) == [], "giriş penceresi aynı"


def test_d4_extended_universe_is_tagged_with_lab_membership(tmp_path):
    book = _book(tmp_path, "d4_donchian_20_10", symbols=[SYM])
    fbs = _d4_fbs((SYM, "SOL/USDT"))
    _step(book, fbs, now_ms=NOW_H4, px=104.1, symbols=[SYM, "SOL/USDT"], learning=_bl("d4_donchian_20_10"))
    inside, outside = book.ledger.positions[SYM], book.ledger.positions["SOL/USDT"]
    assert inside.features["in_lab_universe"] is True and outside.features["in_lab_universe"] is False
    assert "BOOK_UNIVERSE" in outside.meta["learning"]["learning_unlocked_by"]
    assert "BOOK_UNIVERSE" not in inside.meta["learning"]["learning_unlocked_by"]


# ============================================================================ 8) Box zamanlayıcısı: geçiş başına görünüm
class _Prov:
    def __init__(self, fbs: dict):
        self.fbs = fbs

    def klines(self, sym, tf, limit=500, start_ms=None, end_ms=None):
        return self.fbs[sym][tf].copy()


def _timer(book: StrategyBook, learning, fbs: dict, px: float, monkeypatch) -> tuple[BoxTimer, list]:
    t = BoxTimer(book=book, provider_factory=lambda: _Prov(fbs), state_path=book.cfg.state_path,
                 symbols_fn=lambda: [SYM], clock_ms=lambda: NOW_M5, learning=learning)
    now = _dt(NOW_M5)
    monkeypatch.setattr(t, "_marks", lambda prov, syms, now_ms: (
        {s: TickData(last=Decimal(str(px)), mark=Decimal(str(px)), ts=now.isoformat()) for s in syms}, {s: px for s in syms}, {}))
    calls: list = []
    real = book.step

    def spy(**kw):
        calls.append(sorted(kw))
        return real(**kw)
    monkeypatch.setattr(book, "step", spy)
    return t, calls


def test_box_timer_refreshes_learning_once_per_pass_and_passes_the_book_view(tmp_path, monkeypatch):
    fbs = _box_fbs(BOX_TIGHT)
    # kapalı (None): çağrı ve durum dosyası eskisi gibi, tabanda sinyal yok
    book = _book(tmp_path / "off", "b1_box_fade", mode="SHADOW")
    t, calls = _timer(book, None, fbs, 108.3, monkeypatch)
    t.run_once(NOW_M5)
    assert "learning" not in calls[0] and "structures_entry_shadow" not in calls[0] and not book.ledger.positions
    assert "learning" not in json.loads((Path(book.cfg.state_path) / "box_timer.json").read_text(encoding="utf-8"))
    # açık + kapı OK: defter bu geçişin görünümünü alır (min_stop 0,32) → açılır
    gate = {"n": 0}

    def ok_gate():
        gate["n"] += 1
        return True, "OK"
    lm = LearningMode(_section(b1_box_fade={"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32}), mode_gate=ok_gate)
    book = _book(tmp_path / "on", "b1_box_fade", mode="SHADOW")
    t, calls = _timer(book, lm, fbs, 108.3, monkeypatch)
    t.run_once(NOW_M5)
    assert gate["n"] == 1 and "learning" in calls[0] and SYM in book.ledger.positions
    st = json.loads((Path(book.cfg.state_path) / "box_timer.json").read_text(encoding="utf-8"))
    assert st["learning"]["active"] is True and st["learning"]["min_stop_pct"] == 0.32 and st["learning"]["reason"] == "ACTIVE"
    # açık ama mod kapısı düştü: ASKIDA → taban eşiği, işlem yok, durum görünür
    lm2 = LearningMode(_section(b1_box_fade={"enabled": True, "min_stop_pct": 0.32}), mode_gate=lambda: (False, "LIVE_PATH"))
    book = _book(tmp_path / "susp", "b1_box_fade", mode="SHADOW")
    t, calls = _timer(book, lm2, fbs, 108.3, monkeypatch)
    t.run_once(NOW_M5)
    assert not book.ledger.positions and book.learning is None
    st = json.loads((Path(book.cfg.state_path) / "box_timer.json").read_text(encoding="utf-8"))
    assert st["learning"]["active"] is False and st["learning"]["reason"] == "LEARNING_MODE_SUSPENDED:LIVE_PATH"


def test_counters_survive_a_restart(tmp_path):
    syms = ["S%02d/USDT" % i for i in range(3)]
    _flag, brk = _trend_rows()
    book = _book(tmp_path, "t2_trend_regime")
    _step(book, _trend_fbs(brk, syms), now_ms=_asof(brk), px=float(brk[-1]["close"]), symbols=syms,
          learning=_bl("t2_trend_regime"))
    again = _book(tmp_path, "t2_trend_regime")
    assert again.learning_counters.get("opened") == 3 and again.learning is None and again.cf is None
    _step(again, _trend_fbs(brk, syms), now_ms=_asof(brk) + 900_000,
          px=float(brk[-1]["close"]), symbols=syms, learning=_bl("t2_trend_regime"))
    assert again.cf is not None and (again.state_dir / CF_FILE).exists()


def test_counters_survive_an_off_pass_and_a_rollback_rewrite_of_the_summary(tmp_path):
    """(2026-09-28, ikinci doğrulama turu) Özetin `learning` alanı öğrenme kapalı geçişte (ve eski kodda) yazılmaz; sayaçlar
    karşı-olgusal dosyasındaki yedekten (`meta.book_counters`) geri gelir — OFF→ON ya da geri alma→yeniden dağıtım
    sonrası 0'dan başlamaz."""
    syms = ["S%02d/USDT" % i for i in range(3)]
    _flag, brk = _trend_rows()
    fbs, t0, px = _trend_fbs(brk, syms), _asof(brk), float(brk[-1]["close"])
    book = _book(tmp_path, "t2_trend_regime")
    _step(book, fbs, now_ms=t0, px=px, symbols=syms, learning=_bl("t2_trend_regime"))
    assert book.learning_counters.get("opened") == 3
    doc = json.loads((book.state_dir / CF_FILE).read_text(encoding="utf-8"))
    assert doc["meta"]["book_counters"]["opened"] == 3
    off = _book(tmp_path, "t2_trend_regime")                         # yeni süreç, öğrenme KAPALI
    _step(off, fbs, now_ms=t0 + 900_000, px=px, symbols=syms, learning=None)
    summ = json.loads((Path(off.cfg.state_path) / off.summary_file).read_text(encoding="utf-8"))
    assert "learning" not in summ, "kapalı geçiş özeti bugünkü gibi (alan yok)"
    on = _book(tmp_path, "t2_trend_regime")                          # yeniden AÇIK
    assert on.learning_counters == {}
    _step(on, fbs, now_ms=t0 + 1_800_000, px=px, symbols=syms, learning=_bl("t2_trend_regime"))
    assert on.learning_counters.get("opened") == 3, on.learning_counters
    summ = json.loads((Path(on.cfg.state_path) / on.summary_file).read_text(encoding="utf-8"))
    assert summ["learning"]["counters"]["opened"] == 3
