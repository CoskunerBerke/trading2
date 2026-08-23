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
import math
import re
from decimal import Decimal

import pytest

from tradingbot.dashboard.views import (NO_DATA, build, profit_factor_value, summary_cards)
from tradingbot.pnl import (PF_FINITE, PF_NO_CLOSED_TRADES, PF_POSITIVE_INFINITY, PF_UNDEFINED,
                            STOP_RISK_INVALID_ENTRY, STOP_RISK_INVALID_QTY, STOP_RISK_MALFORMED,
                            STOP_RISK_NO_STOP, STOP_RISK_OK, canonical_summary, portfolio_view,
                            position_view, profit_factor_state)

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
    """SENTETİK risk durumu — `RiskEngine.snapshot()` BİÇİMİ, ÜRETİM DEĞERİ DEĞİL.

    ⚠ Buradaki `8.83` / `50.09` / `%293.8` UYDURMA test sayılarıdır; gerçek `state/risk.json`
    ile ilgisi yoktur ve bütçe aşımı yolunu (>%100) zorlamak için seçilmiştir. Gerçeğe yakın
    değerler için `risk_state_realistic` fixture'ına bakın — üretim raporlarında O kullanılmalıdır.
    """
    return {"exposure": {"equity": 50.09, "drawdown_pct": 2.13, "total_open_risk_usdt": 8.83,
                         "open_positions": 2, "used_margin": 28.38861,
                         # motorun YAYIMLADIĞI taban — panel bunu tahmin etmez, okur
                         "starting_equity": 50.09, "equity_basis": 50.09,
                         "equity_basis_kind": "starting_equity",
                         "max_total_open_risk_usdt": 3.0054},
            "profile": {"max_total_open_risk_pct": 6.0, "risk_per_trade_pct": 2.0}}


@pytest.fixture
def risk_state_realistic():
    """GERÇEĞE YAKIN risk durumu — üretimdeki `state/risk.json` şekliyle aynı büyüklükler.

    PAPER_RESEARCH `size_on_live_equity=False` → kabul tabanı `starting_equity`(50.0), canlı
    equity (47.1159) DEĞİL. Bütçe 3.0 USDT, kullanım ≈ %20.3.
    """
    return {"exposure": {"equity": 47.1159, "drawdown_pct": 6.005, "total_open_risk_usdt": 0.6089,
                         "open_positions": 2, "used_margin": 28.38861,
                         "starting_equity": 50.0, "equity_basis": 50.0,
                         "equity_basis_kind": "starting_equity",
                         "max_total_open_risk_usdt": 3.0},
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


@pytest.mark.parametrize("name,trades,num,state,display", [
    # zarar 0 + kâr var → matematiksel ∞. SAYISAL alan `None` (JSON'a `inf` GİREMEZ), UI `∞`.
    ("yalnız kazanç", [{"net_pnl": "5"}, {"net_pnl": "3"}], None, PF_POSITIVE_INFINITY, "∞"),
    ("kazanç + başa baş", [{"net_pnl": "5"}, {"net_pnl": "0"}], None, PF_POSITIVE_INFINITY, "∞"),
    # 0/0 TANIMSIZ — `∞` DEĞİL. Hiç kâr etmemiş bot "sonsuz iyi" görünemez.
    ("yalnız başa baş", [{"net_pnl": "0"}, {"net_pnl": "0"}], None, PF_UNDEFINED, NO_DATA),
    ("kapanmış işlem yok", [], None, PF_NO_CLOSED_TRADES, NO_DATA),
    ("yalnız kayıp", [{"net_pnl": "-5"}], 0.0, PF_FINITE, "0.00"),
    ("karışık", [{"net_pnl": "60"}, {"net_pnl": "-30"}], 2.0, PF_FINITE, "2.00"),
])
def test_profit_factor_contract(name, trades, num, state, display):
    """7b · Profit factor SÖZLEŞMESİ: sayısal alan asla `inf`/`NaN`; anlam `*_state`te taşınır."""
    pv = portfolio_view([], trades)
    assert profit_factor_state(pv) == (num, state), name
    assert profit_factor_value(pv) == num, name
    assert _card_display(pv, "profit_factor") == display, name
    s = canonical_summary(pv)
    assert s["profit_factor"] == num and s["profit_factor_state"] == state, name
    if num is None:
        assert "profit_factor" in s["unavailable_reason"], name


def test_summary_cards_guard_non_finite_even_if_ingest_is_bypassed():
    """JSON SINIRI (views katmanı): `PortfolioView` doğrudan `Decimal("Infinity")` ile kurulsa bile
    hiçbir kart `value`su sonlu-olmayan float taşımaz — `views` kendi `float()`unu DEĞİL, kanonik
    `pnl.finite_float_or_none`u kullanır (ikinci kopya `_num` artık yok)."""
    import dataclasses
    from tradingbot.dashboard import views as V
    from tradingbot import pnl as P
    assert not hasattr(V, "_num"), "views._num geri gelmiş — ikinci kopya yasak"
    assert V.finite_float_or_none is P.finite_float_or_none
    base = portfolio_view([], [{"net_pnl": "60"}, {"net_pnl": "-30"}])
    pv = dataclasses.replace(base, realized_today=Decimal("Infinity"), realized_total=Decimal("-Infinity"),
                             open_net_unrealized=Decimal("NaN"), total_net=Decimal("Infinity"),
                             max_drawdown=Decimal("Infinity"))
    for c in summary_cards(pv):
        assert c.value is None or (isinstance(c.value, (int, float)) and math.isfinite(c.value)), c.key
    for k in ("today_realized_net_usdt", "all_time_realized_net_usdt", "open_net_usdt",
              "total_net_usdt", "max_drawdown_pct"):
        assert {c.key: c for c in summary_cards(pv)}[k].value is None, k
    json.dumps([c.to_dict() for c in summary_cards(pv)], allow_nan=False)


def test_canonical_summary_is_always_rfc_json():
    """JSON SINIRI: hiçbir senaryoda `NaN`/`Infinity` üretilmez (aksi hâlde uç nokta 500 verir)."""
    for trades in ([], [{"net_pnl": "5"}], [{"net_pnl": "0"}], [{"net_pnl": "5"}, {"net_pnl": "-1"}]):
        s = canonical_summary(portfolio_view([], trades))
        json.dumps(s, allow_nan=False)                     # allow_nan=False → inf/NaN'da ValueError
        for k, v in s.items():
            assert not (isinstance(v, float) and not math.isfinite(v)), k


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


@pytest.mark.parametrize("name,kw,risk,status", [
    ("geçerli LONG",      {},                                  Decimal("10"), STOP_RISK_OK),
    ("geçerli SHORT",     {"side": "SHORT", "stop": "110"},     Decimal("10"), STOP_RISK_OK),
    ("ters stop LONG",    {"stop": "110"},                      Decimal("0"),  STOP_RISK_OK),
    ("ters stop SHORT",   {"side": "SHORT", "stop": "90"},      Decimal("0"),  STOP_RISK_OK),
    ("stop yok",          {"stop": None},                       None, STOP_RISK_NO_STOP),
    ("stop boş metin",    {"stop": ""},                         None, STOP_RISK_NO_STOP),
    ("stop bozuk",        {"stop": "abc"},                      None, STOP_RISK_MALFORMED),
    ("stop NaN",          {"stop": "NaN"},                      None, STOP_RISK_MALFORMED),
    ("stop Infinity",     {"stop": "Infinity"},                 None, STOP_RISK_MALFORMED),
    ("stop -Infinity",    {"stop": "-Infinity"},                None, STOP_RISK_MALFORMED),
    ("qty Infinity",      {"qty": "Infinity"},                  None, STOP_RISK_INVALID_QTY),
    ("entry Infinity",    {"entry": "Infinity", "margin": "100"}, None, STOP_RISK_INVALID_ENTRY),
    ("qty sıfır",         {"qty": "0"},                         None, STOP_RISK_INVALID_QTY),
    ("qty negatif",       {"qty": "-1"},                        None, STOP_RISK_INVALID_QTY),
    ("entry bozuk",       {"entry": "abc", "margin": "100"},    None, STOP_RISK_INVALID_ENTRY),
    ("çok küçük Decimal", {"qty": "0.00000001", "entry": "100", "stop": "90"},
                          Decimal("0.00000010"), STOP_RISK_OK),
])
def test_stop_risk_edge_cases(name, kw, risk, status):
    """6 · Bozuk stop `0` KABUL EDİLMEZ — sahte tam-notional risk üretilmez."""
    base = {"sym": "X/USDT", "side": "LONG", "qty": "1", "entry": "100", "stop": "90", "margin": None}
    base.update(kw)
    p = _pos(base["sym"], base["side"], base["qty"], base["entry"], base["stop"], margin=base["margin"])
    if kw.get("stop", "…") is None:
        p["stop"] = None
    v = position_view(p, fees=ZERO_FEES)
    assert v.stop_risk == risk, name
    assert v.stop_risk_status == status, name


def test_malformed_stop_never_produces_full_notional():
    """REGRESYON: `stop="abc"` → `dec()` 0 döndüğü için risk `qty × entry` (tam notional) oluyordu;
    `stop="-Infinity"` ise `Decimal("-Infinity")` olarak KABUL ediliyor ve risk «sonsuz» çıkıyordu."""
    for bad in ("abc", "-Infinity", "Infinity", "NaN"):
        v = position_view(_pos("X/USDT", "LONG", "1", "100", bad), fees=ZERO_FEES)
        assert v.stop_risk is None and v.stop is None and v.stop_risk_status == STOP_RISK_MALFORMED, bad
    p = _pos("X/USDT", "LONG", "1", "100", "abc")
    v = position_view(p, fees=ZERO_FEES)
    assert v.stop_risk is None                       # 100 (tam notional) DEĞİL
    assert v.stop != Decimal("0") and v.stop is None  # sahte `0` stop GÖSTERİLMEZ
    pv = portfolio_view([p, _pos("Y/USDT", "LONG", "1", "100", "90")], [], fees=ZERO_FEES)
    assert pv.open_stop_risk == Decimal("10")        # yalnız geçerli pozisyon toplanır
    assert (pv.positions_stop_malformed, pv.positions_without_stop) == (1, 0)


@pytest.mark.parametrize("field,value", [("last_price", "Infinity"), ("entry_fee", "Infinity"),
                                         ("funding_net", "-Infinity"), ("isolated_margin", "Infinity"),
                                         ("qty", "Infinity"), ("entry_avg", "Infinity")])
def test_position_view_never_crashes_on_infinite_fields(field, value):
    """5 · Infinity içeren pozisyon alanı `Inf×0`/`Inf−Inf` ile `InvalidOperation` FIRLATMAZ;
    sonuç sonludur ve (mark için) «fiyat yok» bayrağı kalkar."""
    p = _pos("X/USDT", "LONG", "1", "100", "90")
    p[field] = value
    v = position_view(p, fees={"taker_pct": 0.05})
    assert v.net_unrealized.is_finite() and v.gross_unrealized.is_finite()
    assert v.stop_risk is None or v.stop_risk.is_finite()
    if field == "last_price":
        assert v.price_is_stale is True and v.mark_price is None


def test_stop_risk_counters_are_labelled_separately():
    """Eksik / bozuk / geçersiz miktar AYRI sayılır — hepsi «stop'suz» sayılmaz."""
    no_stop = _pos("A/USDT", "LONG", "1", "100", "90"); no_stop["stop"] = None
    bad = _pos("B/USDT", "LONG", "1", "100", "abc")
    zero_qty = _pos("C/USDT", "LONG", "0", "100", "90")
    pv = portfolio_view([no_stop, bad, zero_qty], [], fees=ZERO_FEES)
    assert (pv.positions_without_stop, pv.positions_stop_malformed, pv.positions_invalid_qty) == (1, 1, 1)
    assert pv.stop_risk_incomplete == 3
    s = canonical_summary(pv)
    assert s["open_stop_risk_is_partial"] is True
    sub = {c.key: c for c in summary_cards(pv, s)}["open_stop_risk_usdt"].sub
    for marker in ("1 stop'suz", "1 stop değeri bozuk", "1 miktar/giriş geçersiz"):
        assert marker in sub, marker


def test_stop_risk_parity_with_real_engine_reservation():
    """Geçerli pozisyonda panel stop riski = risk motorunun `risk_usdt` formülü (birebir).

    Motor: `|entry−stop|/entry × notional`; panel: `qty × (entry−stop)`; `notional = qty × entry`
    olduğu için ikisi ÖZDEŞ. Değerler gerçek `state/risk.json` kaydından alınmıştır.
    """
    qty, entry, stop = Decimal("0.165"), Decimal("90.61"), Decimal("88.34075519777275")
    v = position_view(_pos("BZ/USDT", "LONG", qty, entry, stop), fees=ZERO_FEES)
    engine_risk = abs(entry - stop) / entry * (qty * entry)      # risk/state.py:build_state
    assert v.stop_risk == pytest.approx(engine_risk)
    assert v.stop_risk == pytest.approx(Decimal("0.37442539236749606"))


def test_summary_stop_risk_equals_row_sum(two_positions, trades_2w_3l, risk_state):
    """13 · Özet stop riski = satırların stop riski toplamı."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    rows = [v.stop_risk for v in vm["portfolio"].positions]
    assert rows == [Decimal("1.00"), Decimal("0.50")]     # 0.5×(90.61−88.61), 0.01×(4479.32−4429.32)
    assert vm["portfolio"].open_stop_risk == sum(rows) == Decimal("1.50")
    assert vm["summary"]["open_stop_risk_usdt"] == pytest.approx(1.50)


def test_reservation_and_stop_risk_are_separate_fields(two_positions, trades_2w_3l, risk_state):
    """14 · Risk motoru rezervasyonu ile stop riski AYRI alanlardır (SENTETİK fixture)."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    s = vm["summary"]
    assert s["risk_engine_reserved_usdt"] == pytest.approx(8.83)   # risk.json → total_open_risk_usdt
    assert s["open_stop_risk_usdt"] == pytest.approx(1.50)         # defterden hesaplanan
    assert s["risk_engine_reserved_usdt"] != s["open_stop_risk_usdt"]
    # Bütçe motorun YAYIMLADIĞI `max_total_open_risk_usdt`tir — panel equity'den TÜRETMEZ.
    assert s["risk_budget_max_usdt"] == pytest.approx(3.0054)
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(293.8, abs=0.5)
    c = _cards(vm)
    assert c["open_stop_risk_usdt"].key != c["risk_engine_reserved_usdt"].key
    assert "stop" in c["open_stop_risk_usdt"].sub.lower()
    assert "total_open_risk_usdt" in c["risk_engine_reserved_usdt"].sub


def test_risk_budget_uses_engine_published_basis(two_positions, trades_2w_3l, risk_state_realistic):
    """14b · GERÇEĞE YAKIN: taban `starting_equity`(50.0) → bütçe 3.0 → kullanım ≈ %20.3.

    Panel canlı equity'yi (47.1159) kullansaydı %21.5 çıkardı; motorun kabul kapısı ise
    `starting_equity` tabanını uygular. Panel motorun yayımladığı tabanı okumak ZORUNDADIR.
    """
    vm = _vm(two_positions, trades_2w_3l, risk_state_realistic)
    s = vm["summary"]
    assert s["risk_equity_basis_usdt"] == pytest.approx(50.0)
    assert s["risk_equity_basis_kind"] == "starting_equity"
    assert s["risk_budget_max_usdt"] == pytest.approx(3.0)
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(20.297, abs=0.01)
    # canlı equity tabanıyla çıkacak YANLIŞ değere DÜŞMEMELİ
    assert s["open_risk_budget_utilization_pct"] != pytest.approx(21.539, abs=0.01)
    sub = _cards(vm)["open_risk_budget_utilization_pct"].sub
    assert "Başlangıç özkaynağı tabanı" in sub and "50.00" in sub


def test_old_snapshot_without_basis_reports_no_data_not_a_guess(two_positions, trades_2w_3l):
    """14c · ESKİ snapshot'ta `max_total_open_risk_usdt` yok → TAHMİN ÜRETİLMEZ, `Veri yok`."""
    old = {"exposure": {"equity": 47.1159, "total_open_risk_usdt": 0.6089},   # taban alanı YOK
           "profile": {"max_total_open_risk_pct": 6.0}}
    vm = _vm(two_positions, trades_2w_3l, old)
    s = vm["summary"]
    assert s["risk_budget_max_usdt"] is None
    assert s["open_risk_budget_utilization_pct"] is None
    assert "risk_budget_max_usdt" in s["unavailable_reason"]
    assert _cards(vm)["open_risk_budget_utilization_pct"].display == NO_DATA
    # eski davranışa (equity × pct) SESSİZCE dönmemeli
    assert s["risk_budget_max_usdt"] != pytest.approx(47.1159 * 0.06)


@pytest.mark.parametrize("age,limit,state,marker", [
    (30, 2400, "live", "risk verisi"),
    (3600, 2400, "stale", "Risk verisi güncel değil"),
    (None, 2400, "unknown", "Risk verisi yaşı bilinmiyor"),
])
def test_risk_snapshot_freshness_is_separate_from_price(two_positions, trades_2w_3l,
                                                        risk_state_realistic, age, limit, state, marker):
    """Risk anlık görüntüsü yaşı AYRI kavramdır; bayatsa değer gösterilir ama ETİKETLENİR."""
    vm = _vm(two_positions, trades_2w_3l, risk_state_realistic, risk_age_s=age, risk_stale_s=limit)
    s = vm["summary"]
    assert s["risk_snapshot_state"] == state
    assert s["risk_snapshot_age_s"] == age
    # bayat olsa bile değer GİZLENMEZ — operatör son bilinen rezervasyonu görmeye devam eder
    assert s["open_risk_budget_utilization_pct"] == pytest.approx(20.297, abs=0.01)
    assert marker in _cards(vm)["open_risk_budget_utilization_pct"].sub


def test_chief_and_summary_never_disagree(two_positions, trades_2w_3l, risk_state):
    """Baş yönetici kartları ile özet kartları AYNI kanonik değerden gelir."""
    vm = _vm(two_positions, trades_2w_3l, risk_state)
    cv, s = vm["chief"], vm["summary"]
    assert cv.margin_util_pct == s["margin_utilization_pct"]
    assert cv.drawdown_pct == s["max_drawdown_pct"]
    assert cv.open_risk_usdt == s["risk_engine_reserved_usdt"]
    assert cv.open_stop_risk_usdt == s["open_stop_risk_usdt"]
