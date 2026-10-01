"""Kanonik PnL katmanı + panel görünüm modeli regresyonları.

Kapatılan üç somut hata:
1. `breadth.long` (LONG **adayı**) ile açık LONG **pozisyon** sayısı aynı şeymiş gibi gösteriliyordu.
2. Panel `pos.get("unrealized")` okuyordu; `Position.to_dict()` böyle bir ANAHTAR üretmez
   (`unrealized` bir METOTTUR) → kod sessizce `realized_pnl`'e (açık pozisyonda 0) düşüyordu.
   Açık pozisyonların sürekli `+0.00` görünmesinin sebebi buydu.
3. `Miktar` sütunu USDT sanılıyordu; aslında coin/kontrat adedidir.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.accounting.models import MarketType, Position, PositionSide  # noqa: E402
from tradingbot.dashboard.views import Freshness, build, chief_view  # noqa: E402
from tradingbot.pnl import (PCT_BASIS_COST, PCT_BASIS_MARGIN, check_invariants, fmt_money,  # noqa: E402
                            fmt_pct, fmt_qty, portfolio_view, position_view, realized_net)


def _pos(**kw) -> dict:
    base = {"id": "F1", "symbol": "ETH/USDT", "side": "LONG", "qty": "10", "entry_avg": "100",
            "leverage": 2, "isolated_margin": "500", "notional": "1000", "stop": "95",
            "targets": ["110"], "entry_fee": "0.5", "funding_paid": "0", "funding_received": "0",
            "opened_at": "2026-08-22T08:00:00+00:00", "market_type": "USDM_PERP"}
    base.update(kw)
    return base


# ===================================================================== 1) PnL formülleri
def test_futures_long_gross_and_net_pnl():
    v = position_view(_pos(), mark_price="103", fees={"taker_pct": "0.05"})
    assert v.gross_unrealized == D("10") * (D("103") - D("100"))          # 30
    exit_fee = D("10") * D("103") * D("0.0005")                            # 0.515
    assert v.exit_fee_est == exit_fee
    assert v.net_unrealized == D("30") - D("0.5") - exit_fee
    assert v.pct_basis == PCT_BASIS_MARGIN
    assert v.net_unrealized_pct == v.net_unrealized / D("500") * D("100")


def test_futures_short_gross_and_net_pnl():
    win = position_view(_pos(side="SHORT", stop="105", targets=["90"]), mark_price="97",
                        fees={"taker_pct": "0.05"})
    assert win.gross_unrealized == D("10") * (D("100") - D("97"))         # +30
    lose = position_view(_pos(side="SHORT"), mark_price="104", fees={"taker_pct": "0.05"})
    assert lose.gross_unrealized == D("10") * (D("100") - D("104"))       # -40
    assert lose.net_unrealized < lose.gross_unrealized                     # ücretler zararı büyütür


def test_futures_long_losing_position():
    v = position_view(_pos(), mark_price="97", fees={"taker_pct": "0.05"})
    assert v.gross_unrealized == D("-30") and v.net_unrealized < D("-30")
    assert v.net_unrealized_pct < 0


def test_spot_pct_basis_is_cost_not_margin():
    v = position_view(_pos(market_type="SPOT", leverage=1, isolated_margin="1000"),
                      mark_price="110", fees={"taker_pct": "0.10"})
    assert v.market == "SPOT" and v.pct_basis == PCT_BASIS_COST
    assert v.net_unrealized_pct == v.net_unrealized / D("1000") * D("100")


@pytest.mark.parametrize("lev", [2, 3, 4, 5])
def test_pct_basis_is_margin_for_every_leverage(lev):
    """Yüzde paydası KULLANILAN TEMİNAT'tır → aynı fiyat hareketi yüksek kaldıraçta daha yüksek %."""
    v = position_view(_pos(leverage=lev, isolated_margin=str(D("1000") / lev)), mark_price="103",
                      fees={"taker_pct": "0.05"})
    assert v.initial_margin == D("1000") / lev
    assert v.net_unrealized_pct == v.net_unrealized / v.initial_margin * D("100")
    # Brüt PnL kaldıraçtan BAĞIMSIZDIR (notional aynı).
    assert v.gross_unrealized == D("30")


def test_funding_and_fees_are_never_double_counted():
    v = position_view(_pos(funding_paid="0.4", funding_received="0.1"), mark_price="100",
                      fees={"taker_pct": "0.05"})
    assert v.funding_net == D("-0.3")
    assert v.net_unrealized == D("0") - D("0.5") - v.exit_fee_est + D("-0.3")


def test_realized_net_does_not_subtract_fees_twice():
    """`realized_pnl` ücreti ZATEN içerir (ledger sözleşmesi) → yalnız funding eklenir."""
    assert realized_net({"realized_pnl": "2.00", "funding_paid": "0.10", "funding_received": "0"}) == D("1.90")
    assert realized_net({"net_pnl": "1.23", "realized_pnl": "9.99"}) == D("1.23")   # net alan kanoniktir


def test_zero_and_tiny_pnl_rendering():
    assert fmt_money(0) == "$0.00"
    assert fmt_money(D("0.004213")) == "+$0.004213"          # `+0.00`'a YUVARLANMAZ
    assert fmt_money(D("-0.0007")) == "-$0.0007"
    assert fmt_money(D("1.24")) == "+$1.24" and fmt_money(D("-0.76")) == "-$0.76"
    assert fmt_pct(D("3.18")) == "+3.18%" and fmt_pct(D("-1.92")) == "-1.92%"
    assert fmt_pct(0) == "0.00%"


def test_qty_is_coin_count_not_usdt():
    assert fmt_qty(D("16.48000000")) == "16.48"
    v = position_view(_pos(qty="16.48", entry_avg="0.91", notional="15.00"), mark_price="0.91")
    assert v.qty == D("16.48") and v.notional == D("15.00")
    assert v.qty != v.notional


def test_missing_mark_is_flagged_stale_not_silently_zero():
    """Fiyat yoksa PnL sessizce `+0.00` GÖSTERİLMEZ; `price_is_stale` ile işaretlenir."""
    v = position_view(_pos(), fees={"taker_pct": "0.05"})
    assert v.price_is_stale is True and v.mark_price is None
    assert v.gross_unrealized == D("0")


def test_position_to_dict_has_no_unrealized_key_regression():
    """Eski panelin okuduğu `unrealized` ANAHTARI hiç var olmadı — kök neden regresyon testi."""
    p = Position(id="F1", symbol="X/USDT", market_type=MarketType.USDM_PERP, side=PositionSide.LONG,
                 qty=D("10"), entry_avg=D("100"), last_price=D("103"))
    d = p.to_dict()
    assert "unrealized" not in d and d["realized_pnl"] == "0"
    v = position_view(d, mark_price=d["last_price"])
    assert v.gross_unrealized == D("30"), "kanonik katman last_price'tan gerçek PnL üretmeli"


def test_pnl_survives_worker_restart():
    """Aynı ledger sözlüğü yeniden yüklendiğinde PnL DEĞİŞMEZ (durum diskten türetilir)."""
    p = Position(id="F1", symbol="X/USDT", market_type=MarketType.USDM_PERP, side=PositionSide.LONG,
                 qty=D("10"), entry_avg=D("100"), leverage=3, isolated_margin=D("333.33"),
                 last_price=D("103"), entry_fee=D("0.5"))
    before = position_view(p.to_dict(), mark_price="103", fees={"taker_pct": "0.05"})
    after = position_view(Position.from_dict(p.to_dict()).to_dict(), mark_price="103",
                          fees={"taker_pct": "0.05"})
    assert before.net_unrealized == after.net_unrealized
    assert before.leverage == after.leverage == 3


def test_unrealized_becomes_realized_consistently():
    v = position_view(_pos(), mark_price="103", fees={"taker_pct": "0.05"})
    closed = {"realized_pnl": format(v.gross_unrealized - v.entry_fee - v.exit_fee_est, "f"),
              "funding_paid": "0", "funding_received": "0"}
    assert realized_net(closed) == v.net_unrealized


# ===================================================================== 2) aday ≠ açık pozisyon
def test_breadth_long_is_candidates_not_open_positions():
    """KULLANICI SENARYOSU: `breadth.long=3` + açık pozisyon 2 → TUTARSIZLIK DEĞİL.

    `breadth.long` son turdaki LONG **aday/karar** sayısıdır; açık pozisyonlar `breadth.hold`
    içinde sayılır ve gerçek sayı DEFTERDEN gelir.
    """
    chief = {"generated_at": "2026-08-22T12:00:00+00:00", "market_risk_mode": "RISK-ON",
             "breadth": {"long": 3, "short": 0, "no_trade": 0, "data_invalid": 0, "hold": 2},
             "exposure": {"risk_used_usdt": 50.0, "margin_util_pct": 44.0, "drawdown_pct": 1.2}}
    positions = [_pos(id="F1", symbol="BZ/USDT", last_price="103"),
                 _pos(id="F2", symbol="XAUT/USDT", last_price="99")]
    vm = build(positions, [], chief, today="2026-08-22")
    cv = vm["chief"]
    assert cv.long_candidates == 3, "aday sayısı chief snapshot'ından gelir"
    assert cv.open_long == 2 and cv.open_short == 0 and cv.open_total == 2, "gerçek sayı defterden"
    assert cv.hold == 2, "açık pozisyonu korunan semboller HOLD'dur"
    assert cv.long_candidates != cv.open_long
    assert vm["inconsistencies"] == [], "bu fark bir veri tutarsızlığı DEĞİLDİR"


def test_open_count_matches_table_rows_invariant():
    positions = [_pos(id=f"F{i}", symbol=f"S{i}/USDT", last_price="101") for i in range(4)]
    vm = build(positions, [], None)
    assert vm["portfolio"].open_total == len(vm["rows"]) == 4
    assert vm["inconsistencies"] == []


def test_long_plus_short_equals_total_invariant():
    positions = [_pos(id="F1", last_price="101"),
                 _pos(id="F2", symbol="B/USDT", side="SHORT", stop="105", last_price="99")]
    pv = portfolio_view(positions, [])
    assert pv.open_long == 1 and pv.open_short == 1 and pv.open_total == 2
    assert check_invariants(pv, table_rows=2) == []


def test_inconsistency_is_reported_not_silently_wrong():
    pv = portfolio_view([_pos(last_price="101")], [])
    issues = check_invariants(pv, table_rows=5)             # tablo ile sayaç uyuşmuyor
    assert issues and issues[0].code == "OPEN_COUNT_TABLE_MISMATCH"


def test_stale_chief_snapshot_with_fresh_ledger():
    """Chief snapshot'ı ESKİ, defter GÜNCEL: açık pozisyon sayısı DEFTERDEN gelir, chief'ten değil."""
    stale_chief = {"generated_at": "2026-08-20T00:00:00+00:00",
                   "breadth": {"long": 9, "short": 4, "no_trade": 1, "data_invalid": 0, "hold": 7}}
    cv = chief_view(stale_chief, portfolio_view([_pos(last_price="101")], []))
    assert cv.long_candidates == 9 and cv.open_long == 1 and cv.open_total == 1
    assert cv.generated_at == "2026-08-20T00:00:00+00:00", "her bölümün veri zamanı görünür"


def test_view_model_has_no_raw_json_blob():
    """Baş yönetici artık ETİKETLİ ALANLAR üretir; ham JSON sözlüğü değil."""
    cv = chief_view({"breadth": {"long": 1}, "exposure": {}}, portfolio_view([], []))
    for f in ("long_candidates", "open_long", "open_total", "market_risk_mode", "generated_at"):
        assert hasattr(cv, f), f
    assert not hasattr(cv, "breadth") and not hasattr(cv, "exposure")


# ===================================================================== 3) tazelik / canlılık
def test_freshness_separates_price_age_from_strategy_run_age():
    fr = Freshness(price_age_s=5, run_age_s=1800, heads_age_s=1800, heartbeat_age_s=10,
                   stale_price_s=90, stale_run_s=2400)
    assert fr.price_state == "live", "fiyat taze"
    assert fr.run_state == "live", "tur yaşı AYRI değerlendirilir"
    stale = Freshness(price_age_s=134, run_age_s=300, heads_age_s=300, heartbeat_age_s=10,
                      stale_price_s=90, stale_run_s=2400)
    assert stale.price_state == "stale" and stale.run_state == "live"
    unknown = Freshness(price_age_s=None, run_age_s=None, heads_age_s=None, heartbeat_age_s=None,
                        stale_price_s=90, stale_run_s=2400)
    assert unknown.price_state == "unknown", "bilinmiyorsa CANLI gösterilmez"


def test_summary_cards_say_no_data_instead_of_fake_zero():
    # Kartlar artık `SummaryCard` nesnesidir: `value` makine için ham sayı, `display` insan için
    # biçimlenmiş metin. (Eski `(başlık, metin, altyazı)` demeti, biçimlenmiş metnin ikinci kez
    # para biçimine sokulup sessizce `$0.00`'a düşmesine yol açıyordu.)
    vm = build([], [], None)                                # hiç işlem yok
    cards = {c.title: c.display for c in vm["cards"]}
    assert cards["Kazanma oranı"] == "Veri yok"
    assert cards["Profit factor"] == "Veri yok"
    assert cards["Maks. drawdown"] == "Veri yok"
    assert cards["Bugün gerçekleşen net K/Z"] == "$0.00"     # gerçekten sıfır
    by_key = {c.key: c for c in vm["cards"]}
    assert by_key["win_rate_pct"].value is None             # hesaplanamadı → null
    assert by_key["today_realized_net_usdt"].value == 0.0   # gerçek sıfır → 0


def test_summary_counts_wins_losses_and_rates():
    trades = [{"realized_pnl": "3", "closed_at": "2026-08-22T01:00:00+00:00"},
              {"realized_pnl": "-1", "closed_at": "2026-08-22T02:00:00+00:00"},
              {"realized_pnl": "0", "closed_at": "2026-08-21T02:00:00+00:00"}]
    pv = portfolio_view([], trades, today="2026-08-22")
    assert (pv.wins, pv.losses, pv.breakeven) == (1, 1, 1)
    assert pv.win_rate == D("50")
    assert pv.profit_factor == D("3")
    assert pv.realized_today == D("2") and pv.realized_total == D("2")
