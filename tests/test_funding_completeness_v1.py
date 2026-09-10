"""FUNDING TAMAMLANMA V1 — "kontrol edildi, settlement yok" ile "kapsama bilinmiyor" AYRI.

Onarilan kusur: uretimde `FundingSchedule.settlement_source = FundingRateCache.settlements_in`
bagliydi ve `settlements_in` IKI farkli durumda ayni bos listeyi donduruyordu:

* aralik tamamen kontrol edildi, gercekten settlement yok;
* araligin venue kapsamasi BILINMIYOR (soguk baslangic, venue erisilemez, kismi pencere).

Ikisi ayni gorundugu icin kapanis kaydina SESSIZ SIFIR geciyordu: `funding=0`, eksiklik bayragi
`0`, uyari yok. Bu dosyadaki testler dort durumu GERCEK uretim kablolamasindan ayirir, eksik
kapanisin ogrenmeye kesinlesmis sonuc olarak girmedigini ve durumun pozisyonlar arasi
SIZMADIGINI sinar.

Kapsam disi (bilerek): venue takvim degisikligi gecikmesi, `futures_backtest` sabit grid'i ve
kapanmis bir kaydin SONRADAN tamamlanmasi. Sonradan tamamlama YOKTUR — bkz.
`Learner._learn_provisional` gerekcesi.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_accounting as TA  # noqa: E402
import test_engine_v3 as TE  # noqa: E402

from tradingbot.accounting import AmountType, SizeSpec, TickData  # noqa: E402
from tradingbot.accounting.models import (FUNDING_COMPLETE, FUNDING_INCOMPLETE, MarketType,  # noqa: E402
                                          SymbolFilters, funding_incomplete, funding_status)
from tradingbot.learn.learner_v2 import LearnerV2  # noqa: E402
from tradingbot.learn.memory import TradeMemory  # noqa: E402
from tradingbot.learn.registry import ModelRegistry  # noqa: E402
from tradingbot.learning import Learner  # noqa: E402
from tradingbot.market.funding_rates import FundingRateCache  # noqa: E402

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"
T_OPEN = datetime(2026, 9, 8, 7, 0, tzinfo=UTC)
T_CLOSE = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)          # 08:00 settlement ARADA kalir
T_SETTLE = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)


class _Venue:
    """Agsiz saglayici: yalniz verilen satirlari, istenen pencereye kirparak dondurur."""

    def __init__(self, rows):
        self.rows = rows

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        return [r for r in self.rows
                if (start_ms is None or r["funding_ts"] >= start_ms)
                and (end_ms is None or r["funding_ts"] <= end_ms)]


def _row(when: datetime, rate: str) -> dict:
    return {"funding_ts": int(when.timestamp() * 1000), "rate": rate, "mark": "3000"}


def _filters(symbol: str = ETH) -> SymbolFilters:
    return SymbolFilters(symbol=symbol, market_type=MarketType.USDM_PERP, price_tick=D("0.01"),
                         qty_step=D("0.001"), min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _wired_ledger(cache: FundingRateCache):
    """URETIMDEKI kablolamanin AYNISI: settlement zamanlari VE kapsama ayni onbellekten.

    `test_engine_wires_both_the_settlement_and_the_coverage_source` bu ikilinin gercekten
    motorda da baglandigini ayrica sinar; burasi yalniz onu taklit eder.
    """
    led = TA._led(equity="500")
    led.funding.settlement_source = cache.settlements_in
    led.funding.coverage_source = cache.covers
    led.funding.require_verified = True
    return led


def _open_then_stop(led, cache, *, t_open=T_OPEN, t_close=T_CLOSE, symbol=ETH):
    """Pozisyon acilir ve stop ile KAPANIR. Funding beklemek kapanisi GECIKTIRMEZ."""
    pos = led.open(symbol, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   filters=_filters(symbol), now=t_open)
    assert pos is not None, led.last_reject_reason
    closed = led.tick({symbol: TickData(last=2890, low=2880, high=2895, bar_open=t_close.isoformat())},
                      now_utc=t_close, funding_rate_lookup=cache.lookup)
    assert closed, "stop TETIKLENMEDI — kurulum yanlis"
    return closed[0]


# ==================================================== 1) kapsama EKSIK
def test_missing_coverage_is_recorded_as_incomplete_not_as_a_verified_zero(tmp_path):
    """Onbellek bos: 08:00 settlement'i BILINMIYOR. Kayit "funding yok" DEMEZ, "bilinmiyor" der."""
    cache = FundingRateCache(tmp_path / "fr.json")
    rec = _open_then_stop(_wired_ledger(cache), cache)
    assert rec.funding == 0                          # uydurma tahakkuk YOK — bu dogru
    assert rec.funding_coverage_gap is True          # ...ama "sifir funding" DEMEK DEGIL
    assert rec.funding_pending_settlements == 0      # kac donem kacti BILINMIYOR: uydurulmadi
    assert rec.funding_incomplete is True
    legacy = rec.to_legacy_dict()
    assert legacy["funding_status"] == FUNDING_INCOMPLETE and legacy["funding_incomplete"] is True
    assert funding_incomplete(legacy) is True


def test_partially_covered_window_is_still_incomplete(tmp_path):
    """Kapsama watermark'tan SONRA basliyorsa aradaki donem bilinmez — kismi kapsama TAM degildir."""
    cache = FundingRateCache(tmp_path / "fr.json")
    # Cekim penceresi 08:30'dan basliyor -> kapsama 07:30'dan onceye UZANMAZ. 08:00 satiri
    # onbellege GIRER (istek bir saat geriden sorar) ama watermark 07:00 kapsanmaz.
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH,
                  datetime(2026, 9, 8, 8, 30, tzinfo=UTC), T_CLOSE)
    rec = _open_then_stop(_wired_ledger(cache), cache)
    assert rec.funding_coverage_gap is True and rec.funding_incomplete is True


def test_a_window_with_no_whole_hour_in_it_cannot_hold_a_settlement(tmp_path):
    """Venue settlement'lari TAM SAATTE yayimlar: icinde tam saat olmayan pencere TAM'dir.

    Bu kural olmadan saniyeler/dakikalar suren HER pozisyon "eksik" damgasi yerdi ve
    ogrenmeden duserdi — olcmedigimiz bir eksikligi ilan etmek, uydurmanin baska bicimidir.
    """
    cache = FundingRateCache(tmp_path / "fr.json")          # ONBELLEK BOS
    t_open = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)
    rec = _open_then_stop(_wired_ledger(cache), cache, t_open=t_open,
                          t_close=t_open + timedelta(minutes=15))
    assert rec.funding_coverage_gap is False and rec.funding_incomplete is False


def test_a_short_window_that_straddles_a_whole_hour_is_still_unknown(tmp_path):
    """Ayirt edici es: ayni uzunlukta ama TAM SAAT iceren pencere BILINMIYOR sayilir."""
    cache = FundingRateCache(tmp_path / "fr.json")          # ONBELLEK BOS
    t_open = datetime(2026, 9, 8, 12, 55, tzinfo=UTC)       # 13:00 ARADA
    rec = _open_then_stop(_wired_ledger(cache), cache, t_open=t_open,
                          t_close=t_open + timedelta(minutes=15))
    assert rec.funding_coverage_gap is True and rec.funding_incomplete is True


# ==================================================== 2) kapsama TAM, settlement YOK
def test_fully_checked_window_without_a_settlement_is_complete(tmp_path):
    """Aralik tamamen kontrol edildi ve gercekten settlement yok: kayit TAM'dir, eksik degil."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH,
                  datetime(2026, 9, 8, 6, tzinfo=UTC), datetime(2026, 9, 8, 10, tzinfo=UTC))
    t_open, t_close = datetime(2026, 9, 8, 8, 30, tzinfo=UTC), datetime(2026, 9, 8, 9, tzinfo=UTC)
    rec = _open_then_stop(_wired_ledger(cache), cache, t_open=t_open, t_close=t_close)
    assert rec.funding == 0                          # 1. testle AYNI sayi...
    assert rec.funding_coverage_gap is False         # ...ama BURADA gercekten sifir
    assert rec.funding_pending_settlements == 0
    assert rec.funding_incomplete is False
    assert rec.to_legacy_dict()["funding_status"] == FUNDING_COMPLETE


# ==================================================== 3) DOGRULANMIS SIFIR oranli settlement
def test_verified_zero_rate_settlement_closes_the_period_and_is_complete(tmp_path):
    """Venue SIFIR yayimladi: dönem KAPANIR, tutar 0'dir ve kayit TAM'dir.

    Bu, 1. testin ayirt edici esidir: ikisinde de `funding == 0`, ama yalniz burada bu bir
    OLCUMDUR. Ayrimi kaldiran bir gerileme iki testten birini dusurur.
    """
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0")]), ETH, T_OPEN, T_CLOSE)
    rec = _open_then_stop(_wired_ledger(cache), cache)
    assert rec.funding == 0
    assert rec.funding_coverage_gap is False and rec.funding_pending_settlements == 0
    assert rec.funding_incomplete is False
    assert funding_status(rec.to_legacy_dict()) == FUNDING_COMPLETE


# ==================================================== 4) DOGRULANMIS SIFIRDAN FARKLI oran
def test_verified_nonzero_rate_settlement_is_accrued_and_complete(tmp_path):
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH, T_OPEN, T_CLOSE)
    rec = _open_then_stop(_wired_ledger(cache), cache)
    assert rec.funding != 0, "dogrulanmis oran TAHAKKUK ETMEDI"
    assert rec.funding_coverage_gap is False and rec.funding_pending_settlements == 0
    assert rec.funding_incomplete is False


def test_known_settlement_without_a_verified_rate_is_pending_not_a_coverage_gap(tmp_path):
    """Settlement zamani BILINIYOR ama oran dogrulanamadi: sayi BILINIR, kapsama boslugu YOK."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH, T_OPEN, T_CLOSE)
    led = _wired_ledger(cache)
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   filters=_filters(), now=T_OPEN)
    assert pos is not None
    closed = led.tick({ETH: TickData(last=2890, low=2880, high=2895, bar_open=T_CLOSE.isoformat())},
                      now_utc=T_CLOSE, funding_rate_lookup=lambda s, w: None)    # oran cozulemedi
    rec = closed[0]
    assert rec.funding_pending_settlements >= 1      # BILINEN sayi
    assert rec.funding_coverage_gap is False         # kapsama vardi; eksik olan orandi
    assert rec.funding_incomplete is True


# ==================================================== uretim kablolamasi
def test_engine_wires_both_the_settlement_and_the_coverage_source(tmp_path, monkeypatch):
    """Motor IKISINI de baglamali. Yalniz `settlement_source` baglanirsa eksiklik gorunmez olur."""
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000)
    assert eng.ledger2.funding.settlement_source == eng.funding_rates.settlements_in
    assert eng.ledger2.funding.coverage_source == eng.funding_rates.covers
    assert eng.ledger2.funding.require_verified is True


# ==================================================== pozisyonlar arasi SIZINTI
def _two_symbol_ledger(tmp_path):
    """AAA kapsanir, ZZZ kapsanmaz. Ikisi de ACIK kalir; kapanis `close_manual` ile olur."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), "AAA/USDT", T_OPEN, T_CLOSE)
    led = _wired_ledger(cache)
    for sym in ("AAA/USDT", "ZZZ/USDT"):
        pos = led.open(sym, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=1000,
                       filters=_filters(sym), now=T_OPEN)
        assert pos is not None, led.last_reject_reason
    # Tek tick: her iki pozisyon icin de tahakkuk denenir; ZZZ EN SON degerlendirilir.
    led.tick({s: TickData(last=3000, bar_open=T_CLOSE.isoformat()) for s in ("AAA/USDT", "ZZZ/USDT")},
             now_utc=T_CLOSE, funding_rate_lookup=cache.lookup)
    return led


def test_manual_close_does_not_inherit_another_symbols_funding_state(tmp_path):
    """`close_manual` tahakkuk CAGIRMAZ: durum paylasilan sayacta dursaydi ZZZ'nin boslugu
    AAA'nin kaydina yazilirdi (ve tersi, sessiz sifir olarak)."""
    led = _two_symbol_ledger(tmp_path)
    covered = led.close_manual("AAA/USDT", 3000, now=T_CLOSE + timedelta(minutes=1))
    uncovered = led.close_manual("ZZZ/USDT", 3000, now=T_CLOSE + timedelta(minutes=2))
    assert covered is not None and uncovered is not None
    assert covered.funding_coverage_gap is False, "kapsanan sembol baska sembolun boslugunu DEVRALDI"
    assert covered.funding_incomplete is False
    assert uncovered.funding_coverage_gap is True, "kapsanmayan sembol EKSIKLIGI KAYBETTI"
    assert uncovered.funding_incomplete is True


def test_a_position_never_evaluated_for_funding_fails_closed(tmp_path):
    """Hic tick gormeden elle kapanan pozisyon: kapsama kaynagi bagliyken "sorun yok" DENMEZ."""
    cache = FundingRateCache(tmp_path / "fr.json")
    led = _wired_ledger(cache)
    assert led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=1000,
                    filters=_filters(), now=T_OPEN) is not None
    rec = led.close_manual(ETH, 3000, now=T_CLOSE)
    assert rec is not None and rec.funding_coverage_gap is True and rec.funding_incomplete is True


def test_grid_scheduled_ledger_reports_no_coverage_gap(tmp_path):
    """Kapsama kaynagi YOKKEN takvim HESAPLANIR: kapsama yapisi geregi tamdir, bosluk uydurulmaz."""
    led = TA._led(equity="500")                       # settlement_source/coverage_source YOK
    assert led.funding.coverage_source is None
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   filters=_filters(), now=T_OPEN)
    assert pos is not None
    closed = led.tick({ETH: TickData(last=2890, low=2880, high=2895, bar_open=T_CLOSE.isoformat())},
                      now_utc=T_CLOSE, funding_rate_lookup=lambda s, w: {"rate": "0.0001", "verified": True})
    assert closed[0].funding_coverage_gap is False


# ==================================================== ogrenme: kesinlesmis sonuc DEGIL
def _legacy_close(*, incomplete: bool, tid: str = "T1", r: float = -1.0) -> dict:
    rec = {"id": tid, "trade_id": tid, "symbol": "AAA/USDT", "side": "LONG", "setup_type": "pullback",
           "pnl": r, "net_pnl": r, "r_multiple": r, "exit_reason": "stop", "fees": 0.0, "funding": 0.0,
           "closed_at": "2026-09-08T09:00:00+00:00", "mae_pct": -1.0, "mfe_pct": 0.5, "bars_held": 4,
           "features": {"regime": "TREND_UP", "setup_type": "pullback", "direction": "LONG"}}
    rec["funding_coverage_gap"] = incomplete
    rec["funding_pending_settlements"] = 0
    rec["funding_incomplete"] = incomplete
    rec["funding_status"] = FUNDING_INCOMPLETE if incomplete else FUNDING_COMPLETE
    return rec


def test_incomplete_close_does_not_move_the_v1_learner_statistics(tmp_path):
    lr = Learner(tmp_path / "learning.json")
    lesson = lr.learn(_legacy_close(incomplete=True))
    assert lesson["provisional"] is True and lesson["learned_into_statistics"] is False
    assert lr.state.n_trades == 0 and lr.state.sum_r == 0.0 and lr.state.setup_stats == {}
    assert lr.state.bias == 0.0 and all(v == 0.0 for v in lr.state.weights.values())
    assert lr.state.lessons, "eksik kapanis GORUNMEZ olmamali — ders yazilmali"
    # KONTROL: ayni kayit TAM oldugunda istatistik ILERLER (test bosluk degil, KAPI).
    lr.learn(_legacy_close(incomplete=False, tid="T2"))
    assert lr.state.n_trades == 1 and lr.state.setup_stats


def test_incomplete_close_is_not_learned_as_a_final_outcome_by_v2(tmp_path):
    lrn = LearnerV2(TradeMemory(tmp_path / "m.jsonl"), ModelRegistry(tmp_path / "md.json"),
                    state_path=tmp_path / "learn_v2.json")
    lesson = lrn.on_trade_closed(_legacy_close(incomplete=True), {"regime": "TREND_UP"})
    assert lesson["provisional"] is True and lesson["learned_into_statistics"] is False
    # PROVENANS: hicbir dugum yazilmadigi icin `learning_keys` URETILMEZ.
    assert "learning_keys" not in lesson
    assert lrn.n_closed == 0
    assert lrn.win.stats == {} and lrn.exp_r.stats == {} and lrn.loss_r.stats == {}
    # KONTROL
    lrn.on_trade_closed(_legacy_close(incomplete=False, tid="T2"), {"regime": "TREND_UP"})
    assert lrn.n_closed == 1 and lrn.win.stats and lrn.exp_r.stats


def test_decision_journal_outcome_carries_the_funding_status():
    from tradingbot.learn.decision_journal import build_outcome_link
    row = build_outcome_link(trade_id="T1", outcome=_legacy_close(incomplete=True),
                             decision_id="D1", lesson={"codes": []})
    assert row["funding_status"] == FUNDING_INCOMPLETE and row["funding_coverage_gap"] is True


def test_quant_journal_flags_an_incomplete_outcome():
    from tradingbot.quant.journal import row_from_memory
    entry = {"trade_id": "T1", "symbol": "AAA/USDT", "side": "LONG", "source": "LIVE_PAPER",
             "plan": {"entry": 3000.0, "stop": 2900.0, "notional": 100.0}}
    row = row_from_memory(entry, {"outcome": _legacy_close(incomplete=True)})
    assert row["funding_complete"] is False and "FUNDING_INCOMPLETE" in row["quality_flags"]
    ok = row_from_memory(entry, {"outcome": _legacy_close(incomplete=False, tid="T2")})
    assert ok["funding_complete"] is True and "FUNDING_INCOMPLETE" not in ok["quality_flags"]
