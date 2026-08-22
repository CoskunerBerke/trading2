"""Panel DOĞRULUK regresyonları — K/Z kartları, risk/teminat kavramları, format ve öğrenme ekranı.

Bu paket YALNIZ salt-okunur gözlem katmanını doğrular. Sinyal üretimi, karar kapıları, giriş/çıkış,
pozisyon boyutlandırma, öğrenme matematiği ve defter muhasebesi TEST EDİLEN DAVRANIŞ DEĞİLDİR ve
bu değişikliklerle DEĞİŞMEMİŞTİR (bkz. `test_algorithm_layer_untouched_by_dashboard`).

Semptom kaynağı (regresyon): `summary_cards()` zaten biçimlenmiş metin döndürüyordu ve panel bunu
ikinci kez `money_html()` ile para biçimine sokuyordu. `Decimal("+$2.86")` çözülemediği için her
kart sessizce `$0.00` oluyordu; sayısal görünen `1.03` ise `+$1.03` gibi PARA gibi gösteriliyordu.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from tradingbot.dashboard.views import (NO_DATA, build, profit_factor_value, summary_cards)
from tradingbot.pnl import canonical_summary, portfolio_view, position_view

ZERO_FEES = {"taker_pct": 0.0}          # ücret etkisini fixture'dan çıkar: net = brüt − açılış ücreti


# --------------------------------------------------------------------------- fixture'lar
def _pos(sym, side, qty, entry, stop, *, tp=None, margin=None, lev=1, last=None, entry_fee="0"):
    return {"id": f"F-{sym}", "symbol": sym, "market_type": "USDM_PERP", "side": side,
            "qty": str(qty), "entry_avg": str(entry), "leverage": lev,
            "isolated_margin": str(margin if margin is not None else Decimal(str(qty)) * Decimal(str(entry)) / lev),
            "stop": str(stop), "targets": [str(tp)] if tp is not None else [],
            "last_price": str(last if last is not None else entry), "entry_fee": entry_fee,
            "opened_at": "2026-08-19T15:39:46+00:00"}


@pytest.fixture
def two_positions():
    """Görüntüdeki iki pozisyon: net açık K/Z +0.25 ve +0.29 → toplam +0.54.

    Ücret sıfırlanır ve `entry_fee=0` verilir; böylece net = brüt = qty × (mark − entry) ve
    beklenen değerler fixture'dan DOĞRUDAN okunur (mark fiyatı sabittir).
    """
    return [
        # 0.25 = 0.5 × (91.11 − 90.61)
        _pos("BZ/USDT", "LONG", "0.5", "90.61", "88.61", tp="95.0", margin="14.95065", last="91.11"),
        # 0.29 = 0.01 × (4508.32 − 4479.32)
        _pos("XAUT/USDT", "LONG", "0.01", "4479.32", "4429.32", tp="4600", margin="13.43796", last="4508.32"),
    ]


@pytest.fixture
def trades_2w_3l():
    """5 kapanmış işlem: 2 kazanan / 3 kaybeden — görüntüdeki değerleri birlikte sağlar.

    brüt kâr  = 60.00 + 34.38 = 94.38
    brüt zarar = 30.00 + 31.52 + 30.00 = 91.52
    net (aynı UTC gün) = 94.38 − 91.52 = +2.86
    profit factor = 94.38 / 91.52 = 1.03125 → «1.03»
    """
    return [
        {"id": "T1", "net_pnl": "60.00", "closed_at": "2026-08-22T10:00:00+00:00"},
        {"id": "T2", "net_pnl": "34.38", "closed_at": "2026-08-22T11:00:00+00:00"},
        {"id": "T3", "net_pnl": "-30.00", "closed_at": "2026-08-22T12:00:00+00:00"},
        {"id": "T4", "net_pnl": "-31.52", "closed_at": "2026-08-22T13:00:00+00:00"},
        {"id": "T5", "net_pnl": "-30.00", "closed_at": "2026-08-22T14:00:00+00:00"},
    ]


@pytest.fixture
def risk_state():
    """Risk motoru durumu — `RiskEngine.snapshot()` biçimi (exposure + profile)."""
    return {"exposure": {"equity": 50.09, "drawdown_pct": 2.13, "total_open_risk_usdt": 8.83,
                         "open_positions": 2, "used_margin": 28.38861},
            "profile": {"max_total_open_risk_pct": 6.0, "risk_per_trade_pct": 2.0}}


def _vm(two_positions, trades, risk_state, **kw):
    return build(two_positions, trades, {"breadth": {"long": 3, "hold": 2}},
                 marks={p["symbol"]: p["last_price"] for p in two_positions}, fees=ZERO_FEES,
                 today="2026-08-22", max_drawdown_pct=risk_state["exposure"].get("drawdown_pct"),
                 futures_equity=risk_state["exposure"].get("equity"), spot_equity=0.0,
                 risk_state=risk_state, **kw)


def _cards(vm):
    return {c.key: c for c in vm["cards"]}


# --------------------------------------------------------------------------- 1-4 · K/Z pariteleri
def test_row_net_pnl_sums_to_summary_card(two_positions, trades_2w_3l, risk_state):
    """1 · Satırlar +$0.25 ve +$0.29 → özet kartı +$0.54 (Decimal hassasiyetinde)."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    nets = [v.net_unrealized for v in vm["portfolio"].positions]
    assert nets == [Decimal("0.25"), Decimal("0.29")]
    assert sum(nets) == Decimal("0.54")
    assert vm["portfolio"].open_net_unrealized == Decimal("0.54")
    assert _cards(vm)["open_net_usdt"].display == "+$0.54"


def test_today_realized_matches_card(two_positions, trades_2w_3l, risk_state):
    """2 · Günlük gerçekleşen +$2.86 → üst kart aynı."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    assert vm["portfolio"].realized_today == Decimal("2.86")
    assert _cards(vm)["today_realized_net_usdt"].display == "+$2.86"


def test_all_time_realized_and_total_net(two_positions, trades_2w_3l, risk_state):
    """3-4 · All-time gerçekleşen doğru; toplam net = all-time + açık."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    pv, s = vm["portfolio"], vm["summary"]
    assert pv.realized_total == Decimal("2.86")          # 60+34.38-30-31.52-30
    assert pv.total_net == pv.realized_total + pv.open_net_unrealized == Decimal("3.40")
    assert s["total_net_usdt"] == pytest.approx(s["all_time_realized_net_usdt"] + s["open_net_usdt"])
    assert _cards(vm)["total_net_usdt"].display == "+$3.40"


def test_older_trade_excluded_from_today_but_in_all_time(two_positions, trades_2w_3l, risk_state):
    """Günlük kırılım UTC KAPANIŞ tarihine göredir; dünkü işlem bugüne sayılmaz."""
    trades = trades_2w_3l + [{"id": "T0", "net_pnl": "5.00", "closed_at": "2026-08-21T23:59:59+00:00"}]
    vm = _vm(two_positions, trades, risk_state)
    assert vm["portfolio"].realized_today == Decimal("2.86")
    assert vm["portfolio"].realized_total == Decimal("7.86")


# --------------------------------------------------------------------------- 5-8 · format
def test_win_loss_pair_and_win_rate(two_positions, trades_2w_3l, risk_state):
    """5-6 · Kazanan/kaybeden `2 / 3`; kazanma oranı `%40.0` — hiçbiri para değil."""
    c = _cards(_vm(two_positions, trades_2w_3l, risk_state))
    assert c["win_loss"].display == "2 / 3"
    assert c["win_rate_pct"].display == "%40.0"
    assert "$" not in c["win_loss"].display and "$" not in c["win_rate_pct"].display


def test_profit_factor_is_ratio_without_currency(two_positions, trades_2w_3l, risk_state):
    """7 · Profit factor `1.03`; `$` YOK, `+` YOK."""
    c = _cards(_vm(two_positions, trades_2w_3l, risk_state))["profit_factor"]
    assert c.display == "1.03"
    assert "$" not in c.display and "+" not in c.display
    assert c.kind == "ratio" and c.signed is False


def test_max_drawdown_is_percent(two_positions, trades_2w_3l, risk_state):
    """8 · Maks. drawdown `%2.13`."""
    assert _cards(_vm(two_positions, trades_2w_3l, risk_state))["max_drawdown_pct"].display == "%2.13"


def test_no_card_is_double_formatted(two_positions, trades_2w_3l, risk_state):
    """REGRESYON: hiçbir kart ikinci kez para biçimine sokulup `$0.00`'a düşmemeli."""
    cards = _vm(two_positions, trades_2w_3l, risk_state)["cards"]
    zeros = [c.key for c in cards if c.display == "$0.00" and c.value not in (0, 0.0)]
    assert zeros == [], f"sessizce $0.00'a düşen kartlar: {zeros}"
    assert not any(c.display.startswith("+$") and c.kind == "ratio" for c in cards)


# --------------------------------------------------------------------------- 9-11 · teminat / veri yok
def test_margin_utilization(two_positions, trades_2w_3l, risk_state):
    """9 · (14.95065 + 13.43796) / 50.09 × 100 ≈ %56.7."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    s = vm["summary"]
    assert s["open_futures_margin_usdt"] == pytest.approx(28.38861)
    assert s["margin_utilization_pct"] == pytest.approx(56.7, abs=0.1)
    assert _cards(vm)["margin_utilization_pct"].display == "%56.7"
    assert vm["chief"].margin_util_pct == pytest.approx(s["margin_utilization_pct"])


def test_zero_equity_reports_no_data_not_zero_pct(two_positions, trades_2w_3l):
    """10 · Sıfır özkaynak → `Veri yok`; `%0.0` GÖSTERİLMEZ."""
    rs = {"exposure": {"equity": 0.0, "total_open_risk_usdt": 1.0}, "profile": {"max_total_open_risk_pct": 6.0}}
    vm = _vm(two_positions, trades_2w_3l, rs)
    assert vm["summary"]["margin_utilization_pct"] is None
    assert "margin_utilization_pct" in vm["summary"]["unavailable_reason"]
    assert _cards(vm)["margin_utilization_pct"].display == NO_DATA


def test_missing_data_is_null_with_reason_not_silent_zero(two_positions, trades_2w_3l):
    """11 · Veri eksikse `null` + neden; sessiz `$0.00`/`%0.0` yok."""
    vm = _vm(two_positions, trades_2w_3l, {"exposure": {}, "profile": {}})
    s = vm["summary"]
    for k in ("margin_utilization_pct", "risk_engine_reserved_usdt", "risk_budget_max_usdt",
              "open_risk_budget_utilization_pct"):
        assert s[k] is None, k
        assert k in s["unavailable_reason"], k
    assert _cards(vm)["risk_engine_reserved_usdt"].display == NO_DATA


def test_profit_factor_special_cases():
    """7b · Zararsız kâr → `∞`; kapanmış işlem yok → `Veri yok`; gerçek 0 → `0.00`."""
    only_wins = portfolio_view([], [{"net_pnl": "5"}, {"net_pnl": "3"}])
    assert profit_factor_value(only_wins) == float("inf")
    assert _card_display(only_wins, "profit_factor") == "∞"
    none_closed = portfolio_view([], [])
    assert profit_factor_value(none_closed) is None
    assert _card_display(none_closed, "profit_factor") == NO_DATA
    only_losses = portfolio_view([], [{"net_pnl": "-5"}])
    assert profit_factor_value(only_losses) == 0.0
    assert _card_display(only_losses, "profit_factor") == "0.00"


def _card_display(pv, key):
    return {c.key: c for c in summary_cards(pv)}[key].display


# --------------------------------------------------------------------------- 12-14 · risk kavramları
@pytest.mark.parametrize("side,entry,stop,qty,expected", [
    ("LONG", "100", "90", "2", Decimal("20")),      # qty × (entry − stop)
    ("SHORT", "100", "110", "2", Decimal("20")),    # qty × (stop − entry)
    ("LONG", "100", "110", "2", Decimal("0")),      # stop lehte → negatif risk YOK
    ("SHORT", "100", "90", "2", Decimal("0")),
])
def test_stop_risk_direction_aware(side, entry, stop, qty, expected):
    """12 · LONG/SHORT stop-risk formülleri; sonuç asla negatif değil."""
    v = position_view(_pos("X/USDT", side, qty, entry, stop), fees=ZERO_FEES)
    assert v.stop_risk == expected


def test_position_without_stop_reports_none_and_partial_flag():
    """Stop'suz pozisyon toplamı KİRLETMEZ; toplam «eksik» olarak işaretlenir."""
    p = _pos("X/USDT", "LONG", "1", "100", "90")
    p["stop"] = None
    pv = portfolio_view([p, _pos("Y/USDT", "LONG", "1", "100", "90")], [], fees=ZERO_FEES)
    assert pv.positions[0].stop_risk is None
    assert pv.open_stop_risk == Decimal("10")
    assert pv.positions_without_stop == 1
    s = canonical_summary(pv)
    assert s["open_stop_risk_is_partial"] is True


def test_summary_stop_risk_equals_row_sum(two_positions, trades_2w_3l, risk_state):
    """13 · Özet stop riski = satırların stop riski toplamı."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    rows = [v.stop_risk for v in vm["portfolio"].positions]
    assert rows == [Decimal("1.00"), Decimal("0.50")]     # 0.5×(90.61−88.61), 0.01×(4479.32−4429.32)
    assert vm["portfolio"].open_stop_risk == sum(rows) == Decimal("1.50")
    assert vm["summary"]["open_stop_risk_usdt"] == pytest.approx(1.50)


def test_reservation_and_stop_risk_are_separate_fields(two_positions, trades_2w_3l, risk_state):
    """14 · Risk motoru rezervasyonu (8.83) ile stop riski (1.50) AYRI alanlardır."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    s = vm["summary"]
    assert s["risk_engine_reserved_usdt"] == pytest.approx(8.83)   # risk.json → total_open_risk_usdt
    assert s["open_stop_risk_usdt"] == pytest.approx(1.50)         # defterden hesaplanan
    assert s["risk_engine_reserved_usdt"] != s["open_stop_risk_usdt"]
    # bütçe = %6 × 50.09 = 3.0054 ; kullanım = 8.83 / 3.0054
    assert s["risk_budget_max_usdt"] == pytest.approx(3.0054)
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(293.8, abs=0.5)
    c = _cards(vm)
    assert c["open_stop_risk_usdt"].key != c["risk_engine_reserved_usdt"].key
    assert "stop" in c["open_stop_risk_usdt"].sub.lower()
    assert "total_open_risk_usdt" in c["risk_engine_reserved_usdt"].sub


def test_chief_and_summary_never_disagree(two_positions, trades_2w_3l, risk_state):
    """Baş yönetici kartları ile özet kartları AYNI kanonik değerden gelir."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    cv, s = vm["chief"], vm["summary"]
    assert cv.margin_util_pct == s["margin_utilization_pct"]
    assert cv.drawdown_pct == s["max_drawdown_pct"]
    assert cv.open_risk_usdt == s["risk_engine_reserved_usdt"]
    assert cv.open_stop_risk_usdt == s["open_stop_risk_usdt"]
