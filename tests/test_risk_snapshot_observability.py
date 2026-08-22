"""`RiskEngine.snapshot()` GÖZLEM alanları — kabul kararı DEĞİŞMEDİ kanıtı.

Panel, toplam açık risk bütçesini `exposure.equity × max_total_open_risk_pct` ile TAHMİN ediyordu.
Motor ise kabul kapısını `equity_basis × max_total_open_risk_pct` ile uygular ve
`size_on_live_equity=False` (PAPER_RESEARCH) iken taban `starting_equity`'dir — CANLI equity değil.
İki taban ayrıştığı için panel motorun gerçekten uyguladığından farklı bir yüzde gösteriyordu.

Bu paket iki şeyi kanıtlar:
  1. `snapshot()` artık motorun ZATEN kullandığı tabanı ve bütçeyi yayımlıyor (salt-okunur).
  2. `evaluate()` kabul/red kararı, boyutlandırma ve `risk_usdt` HİÇ DEĞİŞMEDİ.

Sinyal üretimi, giriş/çıkış, stop/TP ve emir akışı bu paketin kapsamı DIŞINDADIR ve bu
değişiklikle ilgisi yoktur.
"""
from __future__ import annotations

import pytest

from tradingbot.risk import RiskEngine, build_state, resolve_profile

EQUITY_LIVE = 47.1159          # gerçek state/risk.json ile aynı büyüklük
EQUITY_START = 50.0            # gerçek futures_ledger.json → starting_equity


def _state(positions=None, *, equity=EQUITY_LIVE, starting=EQUITY_START):
    return build_state(equity=equity, starting_equity=starting, available=equity,
                       used_margin=0.0, positions=positions or [], history=[],
                       high_water_mark=50.1259)


def _engine(profile_name="PAPER_RESEARCH", **ov):
    return RiskEngine(resolve_profile(profile_name, ov or None, i_understand=True))


def _plan(**kw):
    p = {"symbol": "BZ/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 90.61,
         "stop": 88.34075519777275, "targets": [95.0], "notional": 14.95065, "leverage": 1}
    p.update(kw)
    return p


# --------------------------------------------------------------------------- yayımlanan alanlar
def test_snapshot_publishes_engine_own_basis_and_budget():
    """Yayımlanan taban/bütçe, motorun KABUL kapısında kullandığı değerlerin AYNISI."""
    eng = _engine()
    st = _state()
    exp = eng.snapshot(st)["exposure"]
    assert eng.profile.size_on_live_equity is False              # PAPER_RESEARCH sözleşmesi
    assert exp["equity_basis"] == pytest.approx(EQUITY_START)     # canlı equity DEĞİL
    assert exp["equity_basis_kind"] == "starting_equity"
    assert exp["starting_equity"] == pytest.approx(EQUITY_START)
    assert exp["equity"] == pytest.approx(EQUITY_LIVE)            # canlı equity AYRI alanda korunur
    assert exp["max_total_open_risk_usdt"] == pytest.approx(
        EQUITY_START * eng.profile.max_total_open_risk_pct / 100.0)
    assert exp["max_total_open_risk_usdt"] == pytest.approx(3.0)  # %6 × 50.0


def test_snapshot_basis_follows_profile_flag():
    """`size_on_live_equity=True` profillerde taban CANLI equity olur — sabit varsayım YOK."""
    eng = _engine("TESTNET")
    assert eng.profile.size_on_live_equity is True
    exp = eng.snapshot(_state())["exposure"]
    assert exp["equity_basis"] == pytest.approx(EQUITY_LIVE)
    assert exp["equity_basis_kind"] == "live_equity"
    assert exp["max_total_open_risk_usdt"] == pytest.approx(
        EQUITY_LIVE * eng.profile.max_total_open_risk_pct / 100.0)


def test_published_budget_matches_the_gate_limit_exactly():
    """Yayımlanan bütçe = `TOTAL_OPEN_RISK` kapısının GERÇEK limiti (aynı sayı, iki yer değil)."""
    eng = _engine()
    st = _state()
    d = eng.evaluate(_plan(), st)
    gate = next(c for c in d.checks if c.code == "TOTAL_OPEN_RISK")
    assert gate.limit == pytest.approx(eng.snapshot(st)["exposure"]["max_total_open_risk_usdt"], rel=1e-9)


def test_dashboard_utilisation_matches_engine_not_live_equity():
    """Panelin oranı motorun tabanından çıkar; canlı equity tabanlı YANLIŞ orana düşmez."""
    eng = _engine()
    pos = [{"symbol": "BZ/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 14.95065,
            "margin": 14.95065, "entry": 90.61, "stop": 88.34075519777275, "leverage": 1},
           {"symbol": "XAUT/USDT", "market_type": "USDM_PERP", "side": "LONG", "notional": 13.43796,
            "margin": 13.43796, "entry": 4479.32, "stop": 4401.148684950008, "leverage": 1}]
    exp = eng.snapshot(_state(pos))["exposure"]
    assert exp["total_open_risk_usdt"] == pytest.approx(0.6089, abs=1e-4)
    util = exp["total_open_risk_usdt"] / exp["max_total_open_risk_usdt"] * 100.0
    assert util == pytest.approx(20.297, abs=0.01)                # doğru: starting_equity tabanı
    wrong = exp["total_open_risk_usdt"] / (exp["equity"] * 6.0 / 100.0) * 100.0
    assert wrong == pytest.approx(21.539, abs=0.01)               # eski panel davranışı
    assert util != pytest.approx(wrong, abs=0.5)


# --------------------------------------------------------------------------- davranış paritesi
def _legacy_basis(engine, state) -> float:
    """Değişiklik ÖNCESİ ifade — birebir eski satır (`risk/engine.py` eski hâli)."""
    p = engine.profile
    return state.equity if p.size_on_live_equity else state.starting_equity


@pytest.mark.parametrize("profile", ["PAPER_RESEARCH", "TESTNET", "LIVE", "LIVE_LIMITED"])
def test_equity_basis_is_identical_to_pre_change_expression(profile):
    """Çıkarılan yardımcı, eski satır içi ifadeyle ÖZDEŞ sonuç verir (refactor, davranış değil)."""
    eng = _engine(profile)
    for eq, start in ((EQUITY_LIVE, EQUITY_START), (0.0, 50.0), (120.5, 50.0), (50.0, 50.0)):
        st = _state(equity=eq, starting=start)
        assert eng.equity_basis(st) == _legacy_basis(eng, st)


# Değişiklik ÖNCESİ motorun ürettiği ALTIN değerler (bu commit'ten önce elle kaydedildi).
# Herhangi biri kayarsa kabul davranışı değişmiş demektir.
GOLDEN = [
    # (notional, lev,  stop,                 allowed, reasons,              risk_usdt, adj_not, adj_lev)
    (14.95065, 1, 88.34075519777275, True, [], 0.374425, 14.9506, 1),
    (500.0, 1, 88.34075519777275, False, ["MAX_POSITION_PCT"], 1.0, None, None),
    (14.95065, 20, 88.34075519777275, True, [], 0.374425, 14.9506, 5),
    (14.95065, 1, None, False, ["STOP_PRESENT"], None, None, None),
    (14.95065, 1, 92.0, True, [], 0.22935, 14.9506, 1),
]


@pytest.mark.parametrize("notional,lev,stop,allowed,reasons,risk,adj_not,adj_lev", GOLDEN)
def test_admission_decision_unchanged(notional, lev, stop, allowed, reasons, risk, adj_not, adj_lev):
    """Kabul/red, red gerekçesi, boyutlandırma ve `risk_usdt` DEĞİŞMEDİ (altın değerler)."""
    d = _engine().evaluate(_plan(notional=notional, leverage=lev, stop=stop), _state())
    assert d.allowed is allowed
    assert d.reasons == reasons
    assert d.risk_usdt == (None if risk is None else pytest.approx(risk))
    assert d.adjusted_notional == (None if adj_not is None else pytest.approx(adj_not))
    assert d.adjusted_leverage == adj_lev


@pytest.mark.parametrize("notional,lev,stop,allowed,reasons,risk,adj_not,adj_lev", GOLDEN)
def test_admission_matches_independent_recomputation(notional, lev, stop, allowed, reasons,
                                                     risk, adj_not, adj_lev):
    """Aynı kararlar motor kodu ÇAĞRILMADAN, formüller elle yazılarak da üretilebiliyor."""
    p = _engine().profile
    if stop is None:
        assert allowed is False and reasons == ["STOP_PRESENT"]
        return
    entry, basis = 90.61, EQUITY_START               # size_on_live_equity=False → starting_equity
    stop_frac = abs(entry - stop) / entry            # motor `abs()` kullanır (yön duyarsız)
    exp_risk, exp_notional = notional * stop_frac, notional
    allowed_risk = basis * p.risk_per_trade_pct / 100.0
    if exp_risk > allowed_risk * 1.0001:             # yalnız AŞAĞI boyutlandırma
        exp_notional, exp_risk = allowed_risk / stop_frac, allowed_risk
    assert round(exp_risk, 6) == pytest.approx(risk)
    assert exp_risk <= basis * p.max_total_open_risk_pct / 100.0 + 1e-9   # bütçe kapısı geçer
    if allowed:
        assert round(exp_notional, 4) == pytest.approx(adj_not)
        assert min(lev, p.futures_max_leverage) == adj_lev


def test_total_open_risk_gate_limit_uses_starting_equity_not_live():
    """`TOTAL_OPEN_RISK` limiti canlı equity'den DEĞİL, `starting_equity`'den gelir."""
    eng = _engine()
    d = eng.evaluate(_plan(), _state())
    gate = next(c for c in d.checks if c.code == "TOTAL_OPEN_RISK")
    assert gate.limit == pytest.approx(EQUITY_START * 6.0 / 100.0)        # 3.0
    assert gate.limit != pytest.approx(EQUITY_LIVE * 6.0 / 100.0)         # 2.827 DEĞİL


def test_snapshot_does_not_mutate_state_or_decision():
    """`snapshot()` salt-okunur: durum alanları ve sonraki kabul kararı ETKİLENMEZ."""
    eng = _engine()
    st = _state()
    before = (st.equity, st.starting_equity, st.used_margin, st.total_open_risk_usdt,
              len(st.open_positions))
    d1 = eng.evaluate(_plan(), st)
    eng.snapshot(st)
    eng.snapshot(st)
    d2 = eng.evaluate(_plan(), st)
    after = (st.equity, st.starting_equity, st.used_margin, st.total_open_risk_usdt,
             len(st.open_positions))
    assert before == after
    assert (d1.allowed, d1.reasons, d1.risk_usdt, d1.adjusted_notional, d1.adjusted_leverage) == \
           (d2.allowed, d2.reasons, d2.risk_usdt, d2.adjusted_notional, d2.adjusted_leverage)


def test_snapshot_change_is_additive_only():
    """Eski `exposure` anahtarlarının HEPSİ ve aynı değerlerle duruyor (yalnız EKLEME yapıldı)."""
    eng = _engine()
    st = _state()
    exp = eng.snapshot(st)["exposure"]
    for k in ("equity", "hwm", "drawdown_pct", "open_positions", "total_open_risk_usdt",
              "used_margin", "altcoin_notional", "pnl_today", "pnl_week", "consecutive_losses",
              "positions"):
        assert k in exp, f"eski alan kayboldu: {k}"
    assert exp["equity"] == pytest.approx(st.equity)
    assert exp["total_open_risk_usdt"] == pytest.approx(round(st.total_open_risk_usdt, 4))
    assert exp["drawdown_pct"] == pytest.approx(round(st.drawdown_pct, 3))
