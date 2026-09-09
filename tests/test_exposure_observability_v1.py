"""BRUT MARUZIYET GOZLEMI — stop-risk butcesi ile brut nominal AYRI yayimlanir.

Olculmus durum (2026-09-09, uretim defteri, 14 acik pozisyon):
  · mevcut stoplardan olculen futures stop-riski 5.8321 USDT = baslangic ozkaynaginin %5.83
    (tavan %6 → kapi BAGLAYICI durumdaydi)
  · ayni anda brut nominal 177.45 USDT = ozkaynagin 1.85 kati, kullanilan marj 63.75 USDT (%66.4)
  · 14 pozisyonun 6'sinda stop giriste ya da otesindeydi → stop-riski ~0, butceden yer KAPLAMIYOR

Bu bir KOD kusuru degildir: kapi kendi tanimini dogru uygular. Yayimlanmayan sey, iki buyuklugun
AYRI oldugu ve birinin sifirlanmasinin digerini kucultmedigiydi. Politika degisikligi YOKTUR —
hicbir kapi eklenmedi, hicbir limit degistirilmedi; yalniz `risk.json` artik bu ayrimi gosterir.
"""
from __future__ import annotations

from tradingbot.risk.state import OpenPosition, PortfolioState


def _pos(sym, notional, risk, *, entry=100.0, stop=95.0, mtype="USDM_PERP"):
    return OpenPosition(symbol=sym, market_type=mtype, side="LONG", notional=notional,
                        margin=notional / 3, risk_usdt=risk, entry=entry, stop=stop, leverage=3)


def _state(positions):
    return PortfolioState(equity=100.0, starting_equity=100.0, high_water_mark=100.0,
                          available=40.0, used_margin=60.0, open_positions=positions)


def test_01_gross_notional_is_reported_separately_from_stop_risk():
    st = _state([_pos("A/USDT", 60.0, 1.5), _pos("B/USDT", 120.0, 0.0, stop=100.0)])
    assert st.futures_stop_risk_usdt == 1.5                 # butce yalniz 1.5 gorur
    assert st.futures_notional_usdt == 180.0                # ama nominal 180
    assert st.futures_notional_unknown is False


def test_02_positions_whose_stop_no_longer_risks_anything_are_counted():
    st = _state([_pos("A/USDT", 60.0, 1.5), _pos("B/USDT", 120.0, 0.0, stop=100.0)])
    zero = st.futures_zero_stop_risk
    assert [p.symbol for p in zero] == ["B/USDT"]
    assert sum(p.notional for p in zero) == 120.0


def test_03_spot_positions_are_not_mixed_into_the_futures_numbers():
    st = _state([_pos("A/USDT", 60.0, 1.5),
                 _pos("S/USDT", 500.0, 0.0, mtype="SPOT")])
    assert st.futures_notional_usdt == 60.0
    assert [p.symbol for p in st.futures_zero_stop_risk] == []


def test_04_unmeasurable_notional_is_flagged_not_silently_zero():
    bad = _pos("X/USDT", 0.0, 0.0)
    bad.notional_unknown = True
    st = _state([_pos("A/USDT", 60.0, 1.5), bad])
    assert st.futures_notional_unknown is True


def test_05_snapshot_publishes_the_split_without_changing_any_gate():
    from tradingbot.risk.engine import RiskEngine
    from tradingbot.risk.profiles import PROFILES

    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    st = _state([_pos("A/USDT", 60.0, 1.5), _pos("B/USDT", 120.0, 0.0, stop=100.0)])
    exp = eng.snapshot(st)["exposure"]
    assert exp["futures_notional_usdt"] == 180.0
    assert exp["futures_notional_to_equity"] == 1.8
    assert exp["futures_zero_stop_risk_positions"] == 1
    assert exp["futures_zero_stop_risk_notional_usdt"] == 120.0
    assert exp["futures_stop_risk_usdt"] == 1.5              # kapinin kullandigi deger DEGISMEDI


def test_06_paper_research_profile_still_leaves_margin_and_liq_gates_off():
    """Profil yorumu 'margin, liq buffer ve same-symbol kapilariyla' der; ikisi PAPER_RESEARCH'te
    None'dir, yani HIC UYGULANMAZ. Bu test o ayrimi acikta tutar; deger DEGISTIRILMEDI."""
    from tradingbot.risk.profiles import PROFILES
    p = PROFILES["PAPER_RESEARCH"]
    assert p.futures_margin_utilization_cap_pct is None
    assert p.min_liquidation_buffer_mult is None
    assert p.max_open_positions is None and p.max_positions_per_market is None
    assert p.max_total_open_risk_pct == 6.0 and p.risk_per_trade_pct == 2.0
