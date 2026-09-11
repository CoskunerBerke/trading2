# -*- coding: utf-8 -*-
"""Beklenti alanlarinin BIRIM ve ETIKET sozlesmesi.

Olculmus kusur (e1af166): `coinhead/head.py` `expected_return_net` alanini YUZDE biriminde
uretir (`abs(hedef1-giris)/giris*100 - maliyet`), panel ise ayni degeri `_cell_pct` ile bir kez
daha 100 ile carpardi. Gercek karar kaydindaki 10.8412 ekranda "%1084.12" gorunuyordu.

Bu dosya iki seyi sabitler:
  1. Birim: yuzde birimli alan ikinci kez olceklenmez.
  2. Anlam: "beklenen deger" adi YALNIZ olasilikla agirliklandirilmis buyukluge verilir; plan
     geometrisi (hedef/stop) onun yerine gecmez ve eksik beklenti 0 diye gosterilmez.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.coinhead.head import CoinHead                   # noqa: E402
from tradingbot.coinhead.schema import PlanSize, TradePlanV3   # noqa: E402
from tradingbot.dashboard.views import (COIN_HEAD_COLUMNS, UNKNOWN_EXPECTANCY,  # noqa: E402
                                        _cell_pct, _cell_pct_points, coin_head_table)

#: 2026-09-11 yerel PAPER turundan GERCEK kayitlar (state/coin_heads.json, e1af166).
REAL_LINK_ERN = 11.8648
REAL_SOL_ERN = 8.6016
#: Kullanicinin ekran goruntusundeki ham deger (10.8412 -> "%1084.12" olarak gorunuyordu).
SCREEN_RAW = 10.8412


def _head(**kw):
    h = {"symbol": "LINK/USDT", "verdict": "FUTURES_SHORT", "direction": "SHORT",
         "confidence_calibrated": 0.096, "p_win": 0.5,
         "expected_return_net": REAL_LINK_ERN, "expected_r": 1.972,
         "futures_plan": {"valid": True}, "regime": "TREND"}
    h.update(kw)
    return h


# ---------------------------------------------------------------- 1) birim
def test_percent_valued_field_is_not_scaled_twice():
    assert _cell_pct_points(SCREEN_RAW) == "%10.84"
    assert _cell_pct(SCREEN_RAW) == "%1084.12"          # eski yol: kusurun kaynagi, belgelenir
    assert _cell_pct_points(SCREEN_RAW) != _cell_pct(SCREEN_RAW)


def test_dashboard_cell_shows_plan_return_in_percent_points():
    row = coin_head_table([_head()], [], [])["rows"][0]
    assert row[6] == "%11.86"                            # 11.8648 -> %11.86, "%1186.48" DEGIL
    row_sol = coin_head_table([_head(symbol="SOL/USDT", expected_return_net=REAL_SOL_ERN)], [], [])["rows"][0]
    assert row_sol[6] == "%8.60"


def test_plan_return_matches_head_engine_formula():
    """Panel hucresi motorun URETTIGI buyuklugun ta kendisidir — ikinci bir olcek YOK."""
    entry, t1, cost = 100.0, 110.0, 0.17
    gross = abs(t1 - entry) / entry * 100
    net = round(gross - cost, 4)
    row = coin_head_table([_head(expected_return_net=net)], [], [])["rows"][0]
    assert row[6] == "%9.83"                             # 10.00 - 0.17


# ---------------------------------------------------------------- 2) anlam
def test_expected_r_column_is_labelled_as_geometry_not_expectation():
    assert "E[R]" not in COIN_HEAD_COLUMNS
    assert "Beklenen Net Getiri" not in COIN_HEAD_COLUMNS
    assert "Plan R/R (maliyet sonrası)" in COIN_HEAD_COLUMNS
    assert "Beklenen değer (R)" in COIN_HEAD_COLUMNS


def test_real_expectancy_column_reads_from_opportunity():
    h = _head(opportunity={"net_expectancy_r": 0.486, "conservative_net_edge_r": -0.314,
                           "p_win_calibrated": 0.5})
    row = coin_head_table([h], [], [])["rows"][0]
    assert row[7] == "1.97"                              # plan geometrisi
    assert row[8] == "+0.486"                            # olasilikla agirliklandirilmis beklenti


def test_missing_expectancy_is_unknown_not_zero():
    row = coin_head_table([_head()], [], [])["rows"][0]   # opportunity YOK
    assert row[8] == UNKNOWN_EXPECTANCY
    assert row[8] != "0.000"
    row2 = coin_head_table([_head(opportunity={"net_expectancy_r": None})], [], [])["rows"][0]
    assert row2[8] == UNKNOWN_EXPECTANCY


def test_measured_zero_expectancy_is_shown_as_zero():
    row = coin_head_table([_head(opportunity={"net_expectancy_r": 0.0})], [], [])["rows"][0]
    assert row[8] == "+0.000"                            # OLCULMUS sifir gizlenmez


def test_column_notes_state_the_probability_contract():
    notes = coin_head_table([_head()], [], [])["column_notes"]
    assert "r_multiple > +0.25R" in notes["Beklenen değer (R)"]
    assert "DEĞİLDİR" in notes["Beklenen değer (R)"]
    assert "AĞIRLIKLANDIRILMAMIŞTIR" in notes["Plan getirisi — hedef 1 (maliyet sonrası)"]


# ---------------------------------------------------------------- 3) motor sozlesmesi
def test_head_engine_emits_percent_units_and_constant_geometry():
    """ATR plani hedef1'i stop mesafesinin 2 katina kurar -> `expected_r` ~2 SABITtir."""
    eng = CoinHead.__new__(CoinHead)                     # ctor calistirilmaz: saf hesap testi
    plan = TradePlanV3(market_type="futures", direction="LONG", entry_type="pullback",
                       entry_trigger="", entry_zone=(99.0, 101.0), invalidation="",
                       stop=95.0, targets=[110.0, 115.0], time_horizon_bars=24,
                       size=PlanSize(0.0, "NOTIONAL", 1))
    assert plan.entry == 100.0                           # entry_zone ortasi

    class _Cfg:
        spot_fee_pct = 0.1
        fee_taker_pct = 0.05
        slippage_pct = 0.03
        funding_horizon_bars = 24
        min_expected_r = 1.5
    eng.cfg = _Cfg()
    eng._cost_and_r(plan, "futures", None, None)
    assert plan.expected_cost_pct == 0.16                # (0.05+0.03)*2 — YUZDE puani
    assert abs(plan.expected_r - (10.0 - 0.16) / 5.0) < 1e-9
    gross = abs(plan.targets[0] - plan.entry) / plan.entry * 100
    assert gross == 10.0                                 # yuzde birimi — kesir DEGIL


# ---------------------------------------------------------------- 4) ekonomik kapi: fail-open
def test_zero_probability_is_not_silently_dropped_by_the_economics_gate():
    """`if d.p_win:` YANLISTI — 0.0 falsy oldugu icin "kesin kayip" tahmini DUSERDI.

    Bu, kapinin hiyerarsik prior'a (tipik 0.5) geri donmesi demekti: fail-OPEN. Alan
    `engine_v3:1097`de kalibre degerle EZILDIGI icin head'in ">= 0.5" sezgiseli burada
    gecerli DEGILDIR; sifir ulasilabilir bir degerdir (`round(...,3)` kucuk tahminleri
    0.0'a yuvarlar).
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if isinstance(t, ast.Attribute) and t.attr == "p_win":
            found.append(ast.unparse(t))                      # ciplak truthiness -> KUSUR
    assert not found, "p_win ciplak truthiness ile okunuyor (0.0 sessizce duser): %s" % found
    assert "if d.p_win is not None:" in src


def test_economics_gate_uses_a_zero_probability_when_it_is_given():
    """Sifir olasilik kapiya GIRMELI ve negatif beklenti uretmelidir."""
    from tradingbot.decision_gates import GateLedger
    from tradingbot.opportunity import assess

    a = assess(symbol="X/USDT", side="LONG", setup="pullback", gates=GateLedger(),
               p_win=0.0, avg_win_r=1.96, avg_loss_r=1.0, sample_size=50,
               cost_pct_notional=0.16, stop_dist_pct=4.0)
    assert a.net_expectancy_r < 0 and not a.tradeable
    b = assess(symbol="X/USDT", side="LONG", setup="pullback", gates=GateLedger(),
               p_win=0.5, avg_win_r=1.96, avg_loss_r=1.0, sample_size=50,
               cost_pct_notional=0.16, stop_dist_pct=4.0)
    assert b.net_expectancy_r > a.net_expectancy_r          # 0.0 ile 0.5 AYNI sonucu vermez
