"""SPOT MARUZİYETİ ile FUTURES STOP RİSKİ AYRI BÜTÇELERDİR — davranış testleri.

Kanıtlanan sözleşme (kaynak: `tradingbot/risk/state.py`, `tradingbot/risk/engine.py`):

* `build_state` stop'u OLMAYAN bir pozisyonu TAM NOTIONAL risk sayar
  (`risk = abs(entry-stop)/entry*notional if stop else notional`).
* Eski `TOTAL_OPEN_RISK` kapısı `state.total_open_risk_usdt` (spot+futures BİRLEŞİK) kullanıyordu.
  Bu yüzden 8.226 USDT'lik STOPSUZ BNB spot pozisyonu, 3.0 USDT'lik futures risk bütçesini tek
  başına %294 doldurup her yeni futures adayını bloke ediyordu.
* Yeni sözleşme: kapı adayın KENDİ market kovasını ölçer; spot güvenliği KALDIRILMAZ, ayrı
  `SPOT_ALLOCATION` (notional tavanı) kapısına taşınır.

Gerçek VPS mutabakatı (SHA 7f63490, PAPER, gerçek emir 0) fixture olarak kullanılır.
"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from tradingbot.risk import RiskEngine, build_state, resolve_profile
from tradingbot.risk.state import PortfolioState

# --- DOĞRULANMIŞ VPS DURUMU -------------------------------------------------------------------
# entry değerleri risk.json'daki (notional, risk_usdt, stop) üçlüsünden geri çözüldü:
#   entry = stop / (1 - risk_usdt / notional)
BZ = {"symbol": "BZ/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 14.95065,
      "margin": 14.95065, "entry": 90.61, "stop": 88.34075519777275, "leverage": 1}
XAUT = {"symbol": "XAUT/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 13.43796,
        "margin": 13.43796, "entry": 4479.32, "stop": 4401.148684950008, "leverage": 1}
BNB_SPOT = {"symbol": "BNB/USDT", "market_type": "SPOT", "side": "LONG", "notional": 8.226,
            "margin": 8.226, "entry": 900.0, "stop": None, "leverage": 1}

BZ_RISK = 0.374425
XAUT_RISK = 0.234514
FUT_RISK = 0.608939
SPOT_NOTIONAL = 8.226
COMBINED = 8.834939          # eski (yanlış birleşik) rezervasyon
BUDGET = 3.0                 # starting_equity 50 × max_total_open_risk_pct %6


def _state(positions=(BZ, XAUT, BNB_SPOT), **kw) -> PortfolioState:
    base = dict(equity=47.1159, starting_equity=50.0, available=10.0, used_margin=28.3886,
                positions=list(positions), history=[])
    base.update(kw)
    return build_state(**base)


def _engine(profile="PAPER_RESEARCH", **ov):
    return RiskEngine(resolve_profile(profile, ov or None))


def _plan(symbol="SOL/USDT", market="USDM_PERP", entry=200.0, stop=196.0, notional=10.0, lev=2):
    return {"symbol": symbol, "market_type": market, "direction": "LONG", "entry": entry,
            "stop": stop, "targets": [entry * 1.05], "notional": notional,
            "margin": notional / lev, "leverage": lev, "amount_type": "NOTIONAL",
            "min_notional": 5.0}


def _check(dec, code):
    return next((c for c in dec.checks if c.code == code), None)


# ============================================================== 1) GERÇEK DURUM MUTABAKATI
def test_vps_fixture_reconciles_to_published_numbers():
    st = _state()
    assert st.open_positions[0].risk_usdt == pytest.approx(BZ_RISK, abs=1e-6)
    assert st.open_positions[1].risk_usdt == pytest.approx(XAUT_RISK, abs=1e-6)
    # stopsuz spot HÂLÂ tam notional risk sayılır — bu gerçek gizlenmez
    assert st.open_positions[2].risk_usdt == pytest.approx(SPOT_NOTIONAL, abs=1e-9)
    assert st.total_open_risk_usdt == pytest.approx(COMBINED, abs=1e-5)


def test_three_concepts_are_separate_and_never_summed_into_one():
    st = _state()
    assert st.futures_stop_risk_usdt == pytest.approx(FUT_RISK, abs=1e-6)
    assert st.spot_exposure_usdt == pytest.approx(SPOT_NOTIONAL, abs=1e-9)
    # BNB'nin gerçek/uygulanan stop'u YOK -> "risk azaldı" gibi gösterilmez, AYRI raporlanır
    assert st.spot_stop_risk_usdt == 0.0
    assert st.spot_unbounded_notional_usdt == pytest.approx(SPOT_NOTIONAL, abs=1e-9)
    assert st.spot_symbols_without_stop == ["BNB/USDT"]


def test_futures_budget_utilisation_is_20_30_percent_not_294():
    snap = _engine().snapshot(_state())["exposure"]
    assert snap["max_total_open_risk_usdt"] == pytest.approx(BUDGET, abs=1e-9)
    util = snap["futures_stop_risk_usdt"] / snap["max_total_open_risk_usdt"] * 100.0
    assert util == pytest.approx(20.30, abs=0.01)
    # eski birleşik ölçü hâlâ yayımlanır (raporlama) ama kabul kararını VERMEZ
    assert snap["total_open_risk_usdt"] / BUDGET * 100.0 > 290.0


def test_snapshot_publishes_spot_without_stop_explicitly():
    snap = _engine().snapshot(_state())["exposure"]
    assert snap["spot_symbols_without_stop"] == ["BNB/USDT"]
    assert snap["spot_stop_risk_usdt"] == 0.0
    assert snap["max_spot_allocation_usdt"] == pytest.approx(15.0, abs=1e-9)   # 50 × %30


def test_spot_with_a_real_resting_stop_is_counted_as_bounded_risk():
    """Spot stop'u `SpotLedger.stop_orders()` ile GERÇEKTEN duran emirden gelir; varsa risk ölçülür."""
    bnb_stopped = dict(BNB_SPOT) | {"stop": 855.0}          # %5 stop mesafesi
    st = _state(positions=(BZ, XAUT, bnb_stopped))
    assert st.spot_stop_risk_usdt == pytest.approx(8.226 * 0.05, abs=1e-6)
    assert st.spot_unbounded_notional_usdt == 0.0
    assert st.spot_symbols_without_stop == []
    # stop eklenmesi FUTURES kovasını değiştirmez
    assert st.futures_stop_risk_usdt == pytest.approx(FUT_RISK, abs=1e-6)


# ============================================================== 2) KABUL KAPISI
def test_spot_position_does_not_block_a_futures_candidate():
    """8.83 birleşik rezervasyon yeni futures admission kapısını BLOKE ETMEZ."""
    dec = _engine().evaluate(_plan(), _state())
    tor = _check(dec, "TOTAL_OPEN_RISK")
    assert tor is not None and tor.ok
    assert tor.value == pytest.approx(FUT_RISK + 10.0 * (4.0 / 200.0), abs=1e-3)
    assert "TOTAL_OPEN_RISK" not in dec.reasons


def test_futures_gate_measures_only_futures_bucket():
    with_spot = _check(_engine().evaluate(_plan(), _state()), "TOTAL_OPEN_RISK")
    without_spot = _check(_engine().evaluate(_plan(), _state(positions=(BZ, XAUT))), "TOTAL_OPEN_RISK")
    assert with_spot.value == without_spot.value      # spot kovayı HİÇ etkilemez
    assert with_spot.limit == without_spot.limit


def test_futures_stop_risk_over_budget_is_still_rejected():
    """Sert güvenlik KALDIRILMADI: futures kovası bütçeyi aşarsa aday reddedilir."""
    heavy = dict(BZ) | {"symbol": "ETH/USDT", "notional": 200.0, "entry": 100.0, "stop": 98.5}
    dec = _engine().evaluate(_plan(), _state(positions=(BZ, XAUT, heavy, BNB_SPOT)))
    assert "TOTAL_OPEN_RISK" in dec.reasons and not dec.allowed


def test_spot_allocation_gate_rejects_an_oversized_spot_entry():
    eng = _engine()                                   # tavan: 50 × %30 = 15 USDT
    ok = eng.evaluate(_plan(symbol="ADA/USDT", market="SPOT", entry=1.0, stop=0.97,
                            notional=6.0, lev=1), _state())
    assert _check(ok, "SPOT_ALLOCATION").ok           # 8.226 + 6.0 = 14.226 ≤ 15
    bad = eng.evaluate(_plan(symbol="ADA/USDT", market="SPOT", entry=1.0, stop=0.97,
                             notional=9.0, lev=1), _state())
    assert "SPOT_ALLOCATION" in bad.reasons and not bad.allowed


def test_spot_gate_is_explicit_config_not_derived():
    """Profil alanı None ise kapı UYGULANMAZ — kaynaktan sessiz bir sınır TÜRETİLMEZ."""
    eng = RiskEngine(replace(resolve_profile("PAPER_RESEARCH"), max_spot_allocation_pct=None))
    dec = eng.evaluate(_plan(symbol="ADA/USDT", market="SPOT", entry=1.0, stop=0.97,
                             notional=9.0, lev=1), _state())
    assert _check(dec, "SPOT_ALLOCATION") is None
    assert eng.snapshot(_state())["exposure"]["max_spot_allocation_usdt"] is None


def test_spot_candidate_is_measured_against_the_spot_bucket():
    """Spot adayı futures stop riskini DEVRALMAZ; kendi kovasını ölçer."""
    dec = _engine().evaluate(_plan(symbol="ADA/USDT", market="SPOT", entry=1.0, stop=0.97,
                                   notional=6.0, lev=1), _state())
    tor = _check(dec, "TOTAL_OPEN_RISK")
    assert tor.value == pytest.approx(0.0 + 6.0 * 0.03, abs=1e-6)   # spot_stop_risk (0) + aday riski


# ============================================================== 3) DİFERANSİYEL: AMAÇ DIŞI FARK YOK
_OLD_BEHAVIOUR_CODES = {"KILL_SWITCH_ACTIVE", "STOP_PRESENT", "ALREADY_OPEN_SAME_SYMBOL",
                        "OPPOSITE_EXPOSURE_CONFLICT", "RISK_PER_TRADE", "LEVERAGE_CAP",
                        "MARGIN_UTILIZATION", "LIQ_BUFFER", "SPOT_NO_SHORT", "MAX_POSITION_PCT",
                        "DAILY_LOSS", "WEEKLY_LOSS", "MAX_DRAWDOWN", "CLUSTER_CAP",
                        "ALTCOIN_EXPOSURE", "SPREAD", "MIN_EXPECTED_R", "CONSEC_LOSS_COOLDOWN",
                        "SYMBOL_COOLDOWN", "MIN_ORDER_CONFLICT", "MAX_POSITIONS",
                        "MAX_POSITIONS_MARKET", "TOTAL_OPEN_RISK"}


@pytest.mark.parametrize("profile", ["PAPER_RESEARCH", "TESTNET", "LIVE_LIMITED"])
@pytest.mark.parametrize("market", ["USDM_PERP", "SPOT"])
def test_no_unintended_difference_when_there_is_no_spot_position(profile, market):
    """SPOT pozisyon YOKKEN futures kovası eski birleşik toplamla BİREBİR aynıdır."""
    eng = _engine(profile)
    st = _state(positions=(BZ, XAUT))
    dec = eng.evaluate(_plan(market=market, lev=1 if market == "SPOT" else 2), st)
    tor = _check(dec, "TOTAL_OPEN_RISK")
    if market != "SPOT":
        assert tor.value == pytest.approx(st.total_open_risk_usdt + dec.risk_usdt, abs=1e-4)
    produced = {c.code for c in dec.checks}
    # YALNIZCA bir yeni kod eklenebilir: SPOT_ALLOCATION (ve o da yalnız SPOT adayında)
    assert produced - _OLD_BEHAVIOUR_CODES <= {"SPOT_ALLOCATION"}
    if market != "SPOT":
        assert "SPOT_ALLOCATION" not in produced


def test_only_the_spot_bucket_changes_when_spot_exists():
    """Spot varken futures adayı için HİÇBİR kapının sonucu değişmez."""
    eng = _engine()
    with_spot = {c.code: c.ok for c in eng.evaluate(_plan(), _state()).checks}
    no_spot = {c.code: c.ok for c in eng.evaluate(_plan(), _state(positions=(BZ, XAUT))).checks}
    differing = {k for k in set(with_spot) | set(no_spot) if with_spot.get(k) != no_spot.get(k)}
    assert differing == set()


def test_hard_gates_are_untouched():
    """Kill switch, stop zorunluluğu ve duplicate/aynı sembol kapıları aynen çalışır."""
    from tradingbot.risk.killswitch import KillSwitch
    ks = KillSwitch()
    ks.trip("MAX_DRAWDOWN", "test", source="test")
    eng = RiskEngine(resolve_profile("PAPER_RESEARCH"), ks)
    dec = eng.evaluate(_plan(), _state())
    assert "KILL_SWITCH_ACTIVE" in dec.reasons and not dec.allowed
    eng2 = _engine()
    nostop = eng2.evaluate(_plan() | {"stop": None}, _state())
    assert "STOP_PRESENT" in nostop.reasons
    dup = eng2.evaluate(_plan(symbol="BZ/USDT", entry=90.61, stop=88.34), _state())
    assert "ALREADY_OPEN_SAME_SYMBOL" in dup.reasons


def test_existing_open_positions_are_never_rewritten():
    st = _state()
    before = [p.to_dict() for p in st.open_positions]
    _engine().evaluate(_plan(), st)
    assert [p.to_dict() for p in st.open_positions] == before


# ============================================================== 4) PANEL SÖZLEŞMESİ
def _summary(risk_state):
    from tradingbot.pnl import canonical_summary, portfolio_view
    return canonical_summary(portfolio_view([], []), risk_state=risk_state)


def test_dashboard_summary_is_finite_and_separates_the_three_concepts():
    s = _summary(_engine().snapshot(_state()))
    assert s["futures_stop_risk_usdt"] == pytest.approx(FUT_RISK, abs=1e-6)
    assert s["spot_exposure_usdt"] == pytest.approx(SPOT_NOTIONAL, abs=1e-9)
    assert s["spot_stop_risk_usdt"] == 0.0
    assert s["spot_symbols_without_stop"] == ["BNB/USDT"]
    assert s["futures_risk_budget_utilization_pct"] == pytest.approx(20.30, abs=0.01)
    assert s["spot_allocation_utilization_pct"] == pytest.approx(54.84, abs=0.01)
    for k, v in s.items():
        if isinstance(v, float):
            assert math.isfinite(v), k


def test_old_snapshot_falls_back_to_no_data_not_to_zero():
    """Eski şema risk.json (alanlar YOK) → None + gerekçe. Sessiz 0 ÜRETİLMEZ."""
    legacy = {"exposure": {"equity": 47.1159, "total_open_risk_usdt": 8.8349, "drawdown_pct": 6.0}}
    s = _summary(legacy)
    assert s["futures_stop_risk_usdt"] is None
    assert s["spot_exposure_usdt"] is None
    assert s["futures_risk_budget_utilization_pct"] is None
    assert s["spot_allocation_utilization_pct"] is None
    why = s["unavailable_reason"]
    for k in ("futures_stop_risk_usdt", "spot_exposure_usdt", "spot_stop_risk_usdt"):
        assert k in why and why[k]


def test_chief_view_carries_the_split_without_summing():
    from tradingbot.dashboard.views import chief_view
    from tradingbot.pnl import portfolio_view
    s = _summary(_engine().snapshot(_state()))
    cv = chief_view({}, portfolio_view([], []), s)
    assert cv.futures_stop_risk_usdt == pytest.approx(FUT_RISK, abs=1e-6)
    assert cv.spot_exposure_usdt == pytest.approx(SPOT_NOTIONAL, abs=1e-9)
    assert cv.spot_symbols_without_stop == ["BNB/USDT"]
    # BİRLEŞİK toplam ayrı alanda durur; üç kavram tek sayıya İNDİRGENMEZ
    assert cv.open_risk_usdt == pytest.approx(COMBINED, abs=1e-3)
    assert cv.futures_stop_risk_usdt != cv.open_risk_usdt


# ============================================================== 5) MOTOR SEVİYESİ DİFERANSİYEL
def _spot_burdened_engine(tmp_path, monkeypatch):
    """Gerçek durumun kopyası: 50 USDT özkaynak + 8.226 USDT STOPSUZ BNB spot pozisyonu."""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    from test_engine_v3 import _engine as _build
    from test_risk_capacity_and_gates import _force_triggers
    from tradingbot.risk.state import OpenPosition

    eng = _build(tmp_path, monkeypatch, {"leverage": {"enabled": True}}, symbols=6, equity=50.0)
    _force_triggers(monkeypatch, True)
    orig = eng._portfolio_state

    def patched(marks, _o=orig):
        st = _o(marks)
        st.open_positions.append(OpenPosition(symbol="BNB/USDT", market_type="SPOT", side="LONG",
                                              notional=SPOT_NOTIONAL, margin=SPOT_NOTIONAL,
                                              risk_usdt=SPOT_NOTIONAL, entry=900.0, stop=None,
                                              leverage=1, cluster="exchange"))
        return st

    eng._portfolio_state = patched
    return eng


def test_engine_opens_futures_that_the_combined_bucket_had_blocked(tmp_path, monkeypatch):
    """ÖLÇÜLEN SONUÇ: stopsuz spot varken eski birleşik kova 0 işlem açıyordu, yeni kova açıyor.

    Kalan redler GERÇEK kapasite kapılarıdır — güvenlik gevşetilmedi.
    """
    monkeypatch.setattr(PortfolioState, "open_stop_risk_in",
                        lambda self, mt: self.total_open_risk_usdt)      # 7f63490 davranışı
    old = _spot_burdened_engine(tmp_path / "old", monkeypatch)
    old.tour(do_scan=False, obsidian=False, charts=False)
    old_funnel, old_open = dict(old._funnel), len(old.ledger2.positions)
    monkeypatch.undo()

    new = _spot_burdened_engine(tmp_path / "new", monkeypatch)
    new.tour(do_scan=False, obsidian=False, charts=False)

    assert old_funnel["opened"] == 0 and old_open == 0
    assert new._funnel["opened"] > 0
    assert new._funnel["risk_capacity_blocked"] < old_funnel["risk_capacity_blocked"]
    # AMAÇ DIŞI FARK YOK: huninin üst kapıları birebir aynı kaldı
    for k in ("actionable", "ranked", "trigger_fired", "positive_conservative_edge",
              "research_small", "negative_edge_blocked", "duplicate_blocked",
              "research_policy_blocked", "size_multiplier_zero", "leverage_gate_blocked",
              "exchange_rejected"):
        assert new._funnel[k] == old_funnel[k], k


def test_engine_leverage_stays_inside_two_to_five_with_spot_present(tmp_path, monkeypatch):
    eng = _spot_burdened_engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions, "senaryo geçersiz: hiç pozisyon açılmadı"
    for sym, p in eng.ledger2.positions.items():
        assert 2 <= p.leverage <= 5, (sym, p.leverage)
        # kaldıraç YALNIZ teminatı belirler; stoptaki dolar riski risk bütçesini aşmaz
        risk = abs(float(p.entry_avg) - float(p.stop)) * float(p.qty)
        assert risk <= 50.0 * eng.profile.risk_per_trade_pct / 100.0 + 1e-6, (sym, risk)


def test_bnb_spot_record_is_never_modified_by_the_engine(tmp_path, monkeypatch):
    eng = _spot_burdened_engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    bnb = [p for p in eng._portfolio_state({}).open_positions if p.symbol == "BNB/USDT"]
    assert len(bnb) == 1
    assert (bnb[0].notional, bnb[0].stop, bnb[0].leverage) == (SPOT_NOTIONAL, None, 1)
