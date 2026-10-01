"""Phase 5 — Global Risk Engine, kill switch, profiller, modlar (ağsız)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingbot.core import ConfigError, ExecutionDisabledError
from tradingbot.risk import (PROFILES, GraduationGates, KillSwitch, ModeState, OperatingMode, RiskEngine, build_state,
                             resolve_profile, size_position, warn_if_below_recommended)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _state(equity=50.0, positions=None, history=None, hwm=None, available=None, used=0.0):
    return build_state(equity=equity, starting_equity=50.0, available=available if available is not None else equity, used_margin=used,
                       positions=positions or [], history=history or [], high_water_mark=hwm, now=NOW)


def _plan(**kw):
    p = {"symbol": "ETH/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 3000.0, "stop": 2940.0,
         "notional": 30.0, "margin": 15.0, "leverage": 2, "min_notional": 5.0}
    p.update(kw)
    return p


# ------------------------------------------------------------------ profiller
def test_profiles_legacy_compat_and_validation():
    p = resolve_profile("PAPER_RESEARCH")
    assert p.risk_per_trade_pct == 2.0 and p.max_position_pct == 30.0 and p.futures_max_leverage == 5
    assert p.daily_loss_stop_pct is None and p.max_drawdown_kill_pct is None and not p.size_on_live_equity
    t = resolve_profile("testnet", {"risk_per_trade_pct": 0.4})
    assert t.risk_per_trade_pct == 0.4 and t.futures_max_leverage == 2 and t.daily_loss_stop_pct == 2.0
    with pytest.raises(ConfigError):
        resolve_profile("YOLO")
    with pytest.raises(ConfigError):
        resolve_profile("TESTNET", {"risk_per_trade_pct": 20})
    with pytest.raises(ConfigError):
        resolve_profile("TESTNET", {"futures_max_leverage": 200})
    w = warn_if_below_recommended(resolve_profile("PAPER_RESEARCH"))
    assert any("risk_per_trade_pct" in x for x in w) and any("daily_loss_stop_pct" in x for x in w)


# ------------------------------------------------------------------ kill switch
def test_killswitch_persist_trip_reset_exits_allowed(tmp_path: Path):
    ks = KillSwitch.load(tmp_path / "ks.json")
    assert ks.allows_entry() and ks.state == "ARMED"
    ks.trip("DAILY_LOSS", "−2.1%")
    assert not ks.allows_entry() and ks.allows_exit() and ks.state == "HALT_ENTRIES"
    ks.trip("DB_WRITE_FAILURE", "disk")
    assert ks.state == "HALT_ALL" and ks.allows_exit()
    again = KillSwitch.load(tmp_path / "ks.json")
    assert again.state == "HALT_ALL" and len(again.reasons) == 2
    with pytest.raises(ValueError):
        again.reset("", "")
    again.reset("berke", "incelendi, veri düzeltildi")
    assert again.allows_entry() and again.audit[-1]["action"] == "RESET" and again.audit[-1]["operator"] == "berke"
    assert KillSwitch.load(tmp_path / "ks.json").state == "ARMED"


# ------------------------------------------------------------------ boyutlandırma
def test_size_position_never_inflates_to_min_notional():
    r = size_position(equity=50, risk_pct=0.5, entry=3000, stop=2940, min_notional=5, max_leverage=2, max_position_pct=30)
    assert r.ok and r.notional == pytest.approx(12.5) and r.risk_usdt == pytest.approx(0.25)
    r2 = size_position(equity=50, risk_pct=0.25, entry=3000, stop=2700, min_notional=5, max_leverage=2, max_position_pct=30)
    assert not r2.ok and r2.reason == "NO_TRADE_MIN_ORDER_CONFLICT" and r2.notional < 5
    r3 = size_position(equity=50, risk_pct=1, entry=100, stop=98, min_notional=5, max_leverage=5, max_position_pct=30, liq_buffer_mult=3.0)
    assert r3.ok and r3.leverage == 5     # 1/5−0.004=0.196 ≥ 3×0.02 → 5x kalır
    r4 = size_position(equity=50, risk_pct=1, entry=100, stop=80, min_notional=1, max_leverage=5, max_position_pct=30, liq_buffer_mult=6.0)
    assert r4.leverage == 1 and not r4.ok and r4.reason == "LIQ_BUFFER_TOO_THIN"   # 0.996 < 6×0.2


# ------------------------------------------------------------------ risk engine kontrolleri
def test_engine_allows_legacy_paper_plan_and_adjusts_down():
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    d = eng.evaluate(_plan(), _state())
    assert d.allowed and d.adjusted_notional == 30.0 and d.adjusted_leverage == 2 and d.risk_usdt == pytest.approx(0.6)
    d2 = eng.evaluate(_plan(notional=200.0, leverage=9), _state())
    assert d2.allowed and d2.adjusted_notional < 200.0 and d2.adjusted_leverage == 5   # risk %2 → 1 USDT / %2 stop = 50 notional
    assert d2.adjusted_notional == pytest.approx(50.0) and any(w.startswith("RISK_PER_TRADE") for w in d2.warnings)


def test_engine_each_check_trips():
    prof = PROFILES["TESTNET"]
    eng = RiskEngine(prof, KillSwitch())
    # kill switch
    eng.ks.trip("STALE_DATA")
    assert "KILL_SWITCH_ACTIVE" in eng.evaluate(_plan(), _state()).reasons
    eng.ks.reset("op", "test")
    # stop zorunlu
    assert "STOP_PRESENT" in eng.evaluate(_plan(stop=None), _state()).reasons
    # max positions
    pos = [{"symbol": f"C{i}/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 5, "margin": 5, "entry": 1, "stop": 0.98} for i in range(3)]
    assert "MAX_POSITIONS" in eng.evaluate(_plan(), _state(positions=pos)).reasons
    # aynı sembol açık
    pos = [{"symbol": "ETH/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 5, "margin": 5, "entry": 3000, "stop": 2900}]
    assert "ALREADY_OPEN_SAME_SYMBOL" in eng.evaluate(_plan(), _state(positions=pos)).reasons
    # spot long ↔ futures short çakışması
    pos = [{"symbol": "ETH/USDT", "market_type": "SPOT", "side": "LONG", "notional": 10, "margin": 10, "entry": 3000, "stop": 2900}]
    assert "OPPOSITE_EXPOSURE_CONFLICT" in eng.evaluate(_plan(direction="SHORT"), _state(positions=pos)).reasons
    # toplam açık risk (%2 = 1 USDT): mevcut 0.9 risk + yeni 0.25 → aşım
    pos = [{"symbol": "BTC/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 45, "margin": 45, "entry": 100, "stop": 98}]
    assert "TOTAL_OPEN_RISK" in eng.evaluate(_plan(), _state(positions=pos)).reasons
    # günlük zarar
    hist = [{"symbol": "X/USDT", "closed_at": (NOW - timedelta(hours=1)).isoformat(), "pnl": -1.5}]
    assert "DAILY_LOSS" in eng.evaluate(_plan(), _state(history=hist)).reasons
    # DD kill
    assert "MAX_DRAWDOWN" in eng.evaluate(_plan(), _state(equity=45.0, hwm=50.0)).reasons
    # cluster cap (l1 kümesinde 2 long var → 3. yasak)
    pos = [{"symbol": "SOL/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 5, "margin": 5, "entry": 100, "stop": 99},
           {"symbol": "AVAX/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 5, "margin": 5, "entry": 100, "stop": 99}]
    assert "CLUSTER_CAP" in eng.evaluate(_plan(symbol="ADA/USDT", entry=1, stop=0.99, notional=5), _state(positions=pos)).reasons
    # spread & expected r
    assert "SPREAD" in eng.evaluate(_plan(spread_pct=0.9), _state()).reasons
    assert "MIN_EXPECTED_R" in eng.evaluate(_plan(expected_r=0.3), _state()).reasons
    # cooldown'lar
    hist = [{"symbol": f"L{i}/USDT", "closed_at": (NOW - timedelta(hours=2 + i)).isoformat(), "pnl": -0.1} for i in range(3)]
    assert "CONSEC_LOSS_COOLDOWN" in eng.evaluate(_plan(), _state(history=hist), {"now_utc": NOW}).reasons
    hist = [{"symbol": "ETH/USDT", "closed_at": (NOW - timedelta(hours=3)).isoformat(), "pnl": 0.5}]
    assert "SYMBOL_COOLDOWN" in eng.evaluate(_plan(), _state(history=hist), {"now_utc": NOW}).reasons
    # min emir çatışması: risk %0.5 → 0.25 USDT / stop %10 = 2.5 notional < 5
    d = eng.evaluate(_plan(stop=2700.0, notional=2.5), _state())
    assert "MIN_ORDER_CONFLICT" in d.reasons and d.adjusted_notional is None
    # spot short yasak
    assert "SPOT_NO_SHORT" in eng.evaluate(_plan(market_type="SPOT", direction="SHORT", leverage=1), _state()).reasons


def test_no_martingale_size_after_loss_not_larger():
    eng = RiskEngine(PROFILES["TESTNET"])
    before = eng.evaluate(_plan(notional=100), _state(equity=50)).adjusted_notional
    hist = [{"symbol": "Q/USDT", "closed_at": (NOW - timedelta(days=3)).isoformat(), "pnl": -0.4}]
    after = eng.evaluate(_plan(notional=100), _state(equity=49.6, hwm=50, history=hist)).adjusted_notional
    assert after is not None and after <= before


def test_kill_triggers_from_state_and_health():
    eng = RiskEngine(PROFILES["TESTNET"], KillSwitch())
    hist = [{"symbol": "X/USDT", "closed_at": (NOW - timedelta(hours=1)).isoformat(), "pnl": -1.2}]
    trips = eng.evaluate_kill_triggers(_state(history=hist), {"clock_drift": True})
    assert "DAILY_LOSS" in trips and "CLOCK_DRIFT" in trips and eng.ks.state == "HALT_ALL"


# ------------------------------------------------------------------ modlar
def test_modes_default_paper_transitions_manual_live_disabled(tmp_path: Path, monkeypatch):
    ms = ModeState(tmp_path / "mode.json")
    assert ms.mode == OperatingMode.PAPER and not ms.is_live_order_path_enabled()
    r = ms.request_transition("TESTNET", operator="berke", checks={})
    assert not r.ok and any(x.startswith("TESTNET_GATE") for x in r.reasons) and ms.mode == OperatingMode.PAPER
    r = ms.request_transition("TESTNET", operator="berke", checks={"manual_config": True, "testnet_keys_present": True, "test_suite_passed": True, "health_ok": True})
    assert r.ok and ms.mode == OperatingMode.TESTNET and ModeState(tmp_path / "mode.json").mode == OperatingMode.TESTNET
    r = ms.request_transition("LIVE_LIMITED", operator="berke")   # TESTNET→LIVE_LIMITED yasak sıçrama
    assert not r.ok and any("ILLEGAL_TRANSITION" in x for x in r.reasons)
    ms.request_transition("SHADOW_LIVE", operator="berke", checks={"operator_confirmed": True, "secrets_validated": True, "reconciliation_ok": True, "read_only_permissions": True})
    assert ms.mode == OperatingMode.SHADOW_LIVE
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    r = ms.request_transition("LIVE_LIMITED", operator="berke", checks={"paper_days": 10, "closed_trades": 20})
    assert not r.ok and "ENV_ALLOW_LIVE_TRADING_NOT_TRUE" in r.reasons and "GRADUATION_GATE_PAPER_DAYS" in r.reasons
    # gevşetilmiş kapılar uyarı üretir
    lax = ModeState(tmp_path / "m2.json", GraduationGates(min_paper_days=10, require_manual_confirmation=False))
    r = lax.request_transition("TESTNET", operator="x", checks={})
    assert r.warnings and any("min_paper_days" in w for w in r.warnings)
    # LIVE her koşulda kapalı
    live = ModeState(tmp_path / "m3.json")
    live.mode = OperatingMode.LIVE_LIMITED
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    with pytest.raises(ExecutionDisabledError):
        live.request_transition("LIVE", operator="berke", confirmation_token=ModeState.live_token("default"), config_allow_live=True)
    assert live.mode == OperatingMode.LIVE_LIMITED
