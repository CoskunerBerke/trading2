"""PAPER dinamik kaldıracın KAYNAK KONTROLLÜ etkinleştirilmesi + spot fiyat FAIL-CLOSED.

Kapsanan sözleşmeler:
1. `config.yaml` içinde TEK `leverage:` bölümü; yinelenen anahtar fail-closed reddedilir.
2. Kaldıraç kuralları TEK kanonik yerde (`risk.leverage.validate_leverage_settings`) ve bu kural
   üretim kurulum zincirinde (config yükleyici + `TradingEngineV3.__init__`) GERÇEKTEN çağrılır.
3. `meta.risk_snapshot.max_loss_at_stop_usdt` UYGULANAN notional'dan hesaplanır.
4. Geçersiz spot fiyatı maruziyeti "0" göstermez; yeni SPOT giriş fail-closed reddedilir, yeni
   FUTURES girişi bundan ETKİLENMEZ.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.config import DEFAULT_CONFIG_PATH, load_config, load_yaml_strict  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError  # noqa: E402
from tradingbot.risk import RiskEngine, build_state, resolve_profile, spot_notional_from_prices  # noqa: E402
from tradingbot.risk.leverage import (ABSOLUTE_MAX_LEVERAGE, ABSOLUTE_MIN_LEVERAGE,  # noqa: E402
                                      LeverageConfig, LeverageContext, select_leverage,
                                      validate_leverage_settings)

RAW = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")


# ===================================================================== 1) config.yaml
def test_config_has_exactly_one_leverage_section():
    tops = [ln for ln in RAW.splitlines() if ln.startswith("leverage:")]
    assert len(tops) == 1, tops
    assert load_yaml_strict(RAW)["leverage"]["enabled"] is True


def test_effective_parsed_leverage_values():
    lev = load_v3(load_yaml_strict(RAW)).leverage
    assert (lev.enabled, lev.paper_only, lev.min_leverage, lev.max_leverage) == (True, True, 2, 5)


def test_safety_switches_stay_off_in_config():
    raw = load_yaml_strict(RAW)
    v3 = load_v3(raw)
    assert v3.mode.mode == "PAPER"
    assert v3.mode.live_trading is False
    assert v3.execution.gateway == "paper"
    assert v3.execution.testnet_enabled is False
    assert v3.telegram.enabled is False
    assert v3.monitoring.telegram_enabled is False
    assert load_config(DEFAULT_CONFIG_PATH).v3.leverage.enabled is True


@pytest.mark.parametrize("text", [
    "leverage:\n  enabled: false\nleverage:\n  enabled: true\n",     # ikinci bölüm ilkini ezerdi
    "leverage:\n  enabled: false\n  enabled: true\n",                # iç yinelenen anahtar
])
def test_duplicate_yaml_key_is_fail_closed(text):
    with pytest.raises(ConfigError, match="yinelenen anahtar"):
        load_yaml_strict(text)


def test_real_config_file_loads_under_the_strict_loader():
    assert load_yaml_strict(RAW)["mode"]["mode"] == "PAPER"


# ===================================================================== 2) kanonik doğrulama
@pytest.mark.parametrize("bad,msg", [
    ({"min_leverage": 1}, "1x"),
    ({"max_leverage": 6}, "mutlak üst sınır"),
    ({"min_leverage": 5, "max_leverage": 4}, "min_leverage 5"),
])
def test_config_loader_rejects_out_of_contract_leverage(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        load_v3({"mode": "PAPER", "leverage": {"enabled": True, **bad}})


def test_min_below_two_fails_even_when_disabled():
    """1x sınırı `enabled` bayrağına bağlı DEĞİLDİR — kapalıyken de kabul edilmez."""
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "leverage": {"enabled": False, "min_leverage": 1}})


def test_paper_only_blocks_non_paper_modes():
    with pytest.raises(ConfigError, match="yalnız PAPER"):
        load_v3({"mode": "TESTNET", "leverage": {"enabled": True}})
    assert load_v3({"mode": "TESTNET", "leverage": {"enabled": True, "paper_only": False}}).leverage.enabled


def test_leverage_config_validate_delegates_to_the_same_rules():
    """`LeverageConfig.validate()` artık ölü kod DEĞİL: aynı kanonik kural kümesini çağırır."""
    LeverageConfig(enabled=True).validate(mode="PAPER")
    with pytest.raises(ConfigError):
        LeverageConfig(enabled=True, min_leverage=1).validate()
    with pytest.raises(ConfigError):
        LeverageConfig(enabled=True, max_leverage=6).validate()
    with pytest.raises(ConfigError):
        LeverageConfig(enabled=True, paper_only=True).validate(mode="TESTNET")
    assert (ABSOLUTE_MIN_LEVERAGE, ABSOLUTE_MAX_LEVERAGE) == (2, 5)


def test_engine_construction_runs_the_validation(tmp_path, monkeypatch):
    """MUTASYON KİLİDİ: kütüphane varsayılanı 1x/6x'e kaydığında motor BAŞLAMAZ."""
    with pytest.raises(ConfigError):
        _engine(tmp_path / "a", monkeypatch, {"leverage": {"enabled": True, "min_leverage": 1}}, symbols=2)
    with pytest.raises(ConfigError):
        _engine(tmp_path / "b", monkeypatch, {"leverage": {"enabled": True, "max_leverage": 6}}, symbols=2)
    eng = _engine(tmp_path / "c", monkeypatch, {"leverage": {"enabled": True}}, symbols=2)
    assert eng.leverage_cfg.enabled and eng.leverage_cfg.min_leverage == 2
    assert eng.leverage_cfg.max_leverage == 5 and eng.leverage_cfg.paper_only is True


@pytest.mark.parametrize("mode", ["OBSERVE", "TESTNET", "SHADOW_LIVE"])
def test_leverage_cannot_be_enabled_outside_paper(mode):
    """`paper_only=true` iken PAPER dışı mod config seviyesinde REDDEDİLİR (program başlamaz)."""
    with pytest.raises(ConfigError, match="yalnız PAPER"):
        load_v3({"mode": {"mode": mode}, "leverage": {"enabled": True, "paper_only": True}})


def test_paper_only_gate_disables_selection_if_mode_is_not_paper(tmp_path, monkeypatch):
    """Kapı ifadesi: `enabled and (mode == PAPER or not paper_only)` — PAPER dışında seçim KAPALI."""
    eng = _engine(tmp_path, monkeypatch, {"mode": {"mode": "OBSERVE"},
                                          "leverage": {"enabled": False, "paper_only": True}}, symbols=2)
    assert eng.leverage_cfg.enabled is False
    assert eng.cfg.mode != "PAPER"


# --------------------------------------------------------------- seçim fonksiyonu değer kümesi
def _ctx(**kw) -> LeverageContext:
    base = dict(stop_frac=0.04, atr_pct=3.0, confidence=0.80, conservative_net_edge_r=0.6,
                depth_usdt=500_000.0, spread_pct=0.02, funding_pct=0.005, regime_aligned=True,
                open_risk_frac=0.2, same_direction_open=1, portfolio_corr=0.3)
    base.update(kw)
    return LeverageContext(**base)


def test_selection_returns_only_zero_two_three_four_five():
    cfg = LeverageConfig(enabled=True)
    seen = set()
    for conf in (0.0, 0.10, 0.29, 0.30, 0.44, 0.45, 0.57, 0.58, 0.69, 0.70, 0.99):
        for edge in (None, -1.0, 0.0, 0.14, 0.15, 0.29, 0.30, 0.44, 0.45, 2.0):
            for atr in (0.5, 3.0, 5.0, 7.0, 9.0):
                for extra in ({}, {"portfolio_corr": 0.95}, {"regime_aligned": False},
                              {"depth_usdt": 30_000.0}, {"same_direction_open": 9},
                              {"open_risk_frac": 0.99}, {"funding_pct": 0.9}):
                    d = select_leverage(_ctx(confidence=conf, conservative_net_edge_r=edge,
                                             atr_pct=atr, **extra), cfg)
                    seen.add(d.leverage)
                    assert d.leverage in (0, 2, 3, 4, 5), (conf, edge, atr, extra, d.leverage)
                    assert (d.leverage == 0) == (not d.tradeable)      # 0 YALNIZ NO_TRADE demek
    assert {0, 2, 3, 4, 5} <= seen, seen                                # her bant ULAŞILABİLİR


def test_tier_min_fallback_never_drops_below_min_leverage():
    """`TIER_MIN_FALLBACK` yolu: hiçbir seviye ek koşulları sağlamazsa taban `min_leverage` döner.

    `min_leverage=2` iken bu yol ULAŞILMAZDIR (2x'in ek koşulu yoktur → `TIER_2X_SATISFIED`).
    Yol ancak `min_leverage >= 3` iken çalışır; kritik değişmez: fallback ASLA `min_leverage`in
    altına — özellikle 1x'e — düşmez.
    """
    # stop_frac/atr orani taban araliginda (0.5..4.0) kalir; atr %8.5 > 8.0 -> 3x/4x/5x duser
    weak = _ctx(confidence=0.31, conservative_net_edge_r=0.0, atr_pct=8.5, stop_frac=0.05)
    base = select_leverage(weak, LeverageConfig(enabled=True))
    assert base.leverage == ABSOLUTE_MIN_LEVERAGE == 2 and "TIER_2X_SATISFIED" in base.reasons
    fb = select_leverage(weak, LeverageConfig(enabled=True, min_leverage=3, max_leverage=5))
    assert "TIER_MIN_FALLBACK" in fb.reasons and fb.leverage == 3
    assert fb.leverage >= ABSOLUTE_MIN_LEVERAGE


def test_ledger_positions_only_carry_allowed_leverage(tmp_path, monkeypatch):
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    eng = _engine(tmp_path, monkeypatch, {"leverage": {"enabled": True}}, symbols=6, equity=5_000.0)
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions, "senaryo geçersiz: hiç pozisyon açılmadı"
    for sym, p in eng.ledger2.positions.items():
        assert p.leverage in (2, 3, 4, 5), (sym, p.leverage)


# ===================================================================== 3) risk_snapshot metadata
def _tour(tmp_path, monkeypatch, equity=50.0, **ov):
    from test_risk_capacity_and_gates import _force_triggers
    eng = _engine(tmp_path, monkeypatch, {"leverage": {"enabled": True}, **ov}, symbols=6, equity=equity)
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    return eng


def test_metadata_max_loss_uses_the_applied_notional(tmp_path, monkeypatch):
    """Metadata'daki azami zarar = LEDGER'a giden notional'ın stop riski (istenen değil)."""
    eng = _tour(tmp_path, monkeypatch)
    assert eng.ledger2.positions
    for sym, p in eng.ledger2.positions.items():
        snap = (p.meta or {}).get("risk_snapshot") or {}
        applied, frac = snap["applied_notional"], snap["stop_frac"]
        real = abs(float(p.entry_avg) - float(p.stop)) * float(p.qty)
        # 1) uygulanan (RiskEngine sözleşmesi) değer AYRI alanda ve `rd.risk_usdt` ile aynı
        assert snap["applied_risk_usdt"] == pytest.approx(applied * frac, abs=1e-6), sym
        assert snap["applied_risk_usdt"] == pytest.approx(snap["risk_engine_risk_usdt"], rel=2e-3), sym
        # 2) yayımlanan azami zarar DEFTERİN GERÇEKTEN AÇTIĞI pozisyondan gelir (lot yuvarlaması dâhil)
        assert snap["max_loss_at_stop_usdt"] == pytest.approx(real, abs=1e-6), sym
        assert snap["filled_notional"] <= snap["applied_notional"] + 1e-9
        # istenen (küçültme öncesi) değer AYRI alanda saklanır ve küçültülmüş olandan küçük değildir
        assert snap["requested_risk_usdt"] >= snap["max_loss_at_stop_usdt"] - 1e-9
        assert snap["initial_margin"] == pytest.approx(snap["filled_notional"] / p.leverage, rel=1e-6)


def test_metadata_reports_the_reduced_risk_not_the_requested_one(tmp_path, monkeypatch):
    """RISK_PER_TRADE küçültmesi devredeyken metadata ARTIK şişik değer bildirmez."""
    eng = _tour(tmp_path, monkeypatch)
    reduced = [(s, (p.meta or {}).get("risk_snapshot") or {}) for s, p in eng.ledger2.positions.items()]
    reduced = [(s, m) for s, m in reduced if m["requested_notional"] > m["applied_notional"] + 1e-9]
    assert reduced, "senaryo geçersiz: hiçbir aday küçültülmedi"
    cap = 50.0 * eng.profile.risk_per_trade_pct / 100.0
    for s, m in reduced:
        assert m["max_loss_at_stop_usdt"] < m["requested_risk_usdt"], s
        assert m["max_loss_at_stop_usdt"] <= cap + 1e-6, s


@pytest.mark.parametrize("side,entry,stop", [("LONG", 200.0, 196.0), ("SHORT", 200.0, 204.0)])
def test_applied_risk_contract_holds_for_long_and_short(side, entry, stop):
    """Metadata'nın kullandığı formül YÖNDEN BAĞIMSIZDIR: applied_notional × |entry−stop|/entry."""
    eng = RiskEngine(resolve_profile("PAPER_RESEARCH"))
    st = build_state(equity=50.0, starting_equity=50.0, available=50.0, used_margin=0.0,
                     positions=[], history=[])
    plan = {"symbol": "SOL/USDT", "market_type": "USDM_PERP", "direction": side, "entry": entry,
            "stop": stop, "targets": [entry * (1.05 if side == "LONG" else 0.95)],
            "notional": 400.0, "margin": 100.0, "leverage": 4, "amount_type": "NOTIONAL",
            "min_notional": 5.0}
    rd = eng.evaluate(plan, st)
    applied = min(400.0, rd.adjusted_notional)
    frac = abs(entry - stop) / entry
    assert applied * frac == pytest.approx(rd.risk_usdt, rel=1e-9)
    assert rd.risk_usdt == pytest.approx(50.0 * 2.0 / 100.0, rel=1e-9)     # %2 tavanına küçültüldü


# ===================================================================== 4) spot fiyat fail-closed
@pytest.mark.parametrize("mark", [None, 0, -1.0, float("nan"), float("inf"), float("-inf"), "abc", ""])
def test_invalid_mark_falls_back_to_cost_basis(mark):
    notional, unknown = spot_notional_from_prices(2.0, mark, 100.0)
    assert (notional, unknown) == (200.0, False)          # sessiz 0 YOK


@pytest.mark.parametrize("mark", [None, 0, float("nan"), float("inf"), "abc"])
@pytest.mark.parametrize("cost", [None, 0, float("nan"), float("-inf"), "abc"])
def test_both_prices_invalid_marks_exposure_unknown(mark, cost):
    notional, unknown = spot_notional_from_prices(2.0, mark, cost)
    assert unknown is True and notional == 0.0


def test_valid_mark_keeps_existing_behaviour():
    assert spot_notional_from_prices(3.0, 10.0, 8.0) == (30.0, False)
    assert spot_notional_from_prices(0, 10.0, 8.0) == (0.0, False)         # pozisyon yok


_BZ = {"symbol": "BZ/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 14.95065,
       "margin": 14.95065, "entry": 90.61, "stop": 88.34075519777275, "leverage": 1}


def _state_with_bad_spot():
    bad = {"symbol": "BNB/USDT", "market_type": "SPOT", "side": "LONG", "notional": 0.0,
           "margin": 8.226, "entry": 900.0, "stop": None, "leverage": 1, "notional_unknown": True}
    return build_state(equity=47.0, starting_equity=50.0, available=10.0, used_margin=14.95,
                       positions=[_BZ, bad], history=[])


def _plan(market="USDM_PERP", **kw):
    p = {"symbol": "SOL/USDT", "market_type": market, "direction": "LONG", "entry": 200.0,
         "stop": 196.0, "targets": [210.0], "notional": 10.0, "margin": 5.0, "leverage": 2,
         "amount_type": "NOTIONAL", "min_notional": 5.0}
    p.update(kw)
    return p


def test_unknown_spot_exposure_blocks_new_spot_entry():
    eng = RiskEngine(resolve_profile("PAPER_RESEARCH"))
    st = _state_with_bad_spot()
    assert st.spot_exposure_unknown is True and st.spot_symbols_unknown_price == ["BNB/USDT"]
    dec = eng.evaluate(_plan("SPOT", symbol="ADA/USDT", entry=1.0, stop=0.97, notional=1.0, leverage=1), st)
    assert "SPOT_ALLOCATION" in dec.reasons and not dec.allowed
    chk = next(c for c in dec.checks if c.code == "SPOT_ALLOCATION")
    assert chk.value is None and "BİLİNMİYOR" in chk.note


def test_unknown_spot_exposure_does_not_block_futures():
    """Bilinmeyen spot maruziyeti futures adayını BİRLEŞİK kova üzerinden tekrar bloke ETMEZ."""
    eng = RiskEngine(resolve_profile("PAPER_RESEARCH"))
    dec = eng.evaluate(_plan(), _state_with_bad_spot())
    tor = next(c for c in dec.checks if c.code == "TOTAL_OPEN_RISK")
    assert tor.ok and tor.value == pytest.approx(0.374425 + 10.0 * 0.02, abs=1e-4)
    assert not any(c.code == "SPOT_ALLOCATION" for c in dec.checks)
    assert dec.allowed


def test_snapshot_and_summary_report_no_data_with_a_reason():
    from tradingbot.pnl import canonical_summary, portfolio_view
    snap = RiskEngine(resolve_profile("PAPER_RESEARCH")).snapshot(_state_with_bad_spot())
    exp = snap["exposure"]
    assert exp["spot_exposure_usdt"] is None and exp["spot_exposure_unknown"] is True
    assert exp["spot_symbols_unknown_price"] == ["BNB/USDT"]
    s = canonical_summary(portfolio_view([], []), risk_state=snap)
    assert s["spot_exposure_usdt"] is None and s["spot_allocation_utilization_pct"] is None
    assert "geçersiz" in s["unavailable_reason"]["spot_exposure_usdt"]
    for k, v in s.items():
        if isinstance(v, float):
            assert math.isfinite(v), k


def test_dashboard_note_names_the_bad_price_symbol():
    from tradingbot.dashboard.views import spot_stop_note
    note = spot_stop_note({"spot_exposure_unknown": True, "spot_symbols_unknown_price": ["BNB/USDT"]})
    assert "BNB/USDT" in note and "fiyat geçersiz" in note


def test_engine_portfolio_state_marks_unpriceable_spot(tmp_path, monkeypatch):
    """Uçtan uca: defterde spot var, mark yok/bozuk → maruziyet BİLİNMİYOR olarak işaretlenir."""
    from decimal import Decimal as D
    eng = _engine(tmp_path, monkeypatch, {"leverage": {"enabled": True}}, symbols=2, equity=50.0)
    eng.spot2.positions = lambda: {"BNB/USDT": {"symbol": "BNB/USDT", "qty": D("0.01"),
                                                "avg_cost": D("0"), "entry_time": "", "stop": 0.0}}
    st = eng._portfolio_state({"BNB/USDT": float("nan")})
    assert st.spot_exposure_unknown is True
    assert st.futures_stop_risk_usdt == 0.0        # futures kovası etkilenmez
