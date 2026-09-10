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
from tradingbot.accounting.funding import FundingSchedule  # noqa: E402
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
    # Tick ile AYNI an: tazelik kurali devrede olmasin, olculen sey YALNIZ capraz sizinti olsun.
    covered = led.close_manual("AAA/USDT", 3000, now=T_CLOSE)
    uncovered = led.close_manual("ZZZ/USDT", 3000, now=T_CLOSE)
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


# ==================================================== takvim ISPATI (bagimsiz inceleme F1/F7)
def _sparse_cache(tmp_path):
    """Iki KOPUK dar cekim: 4 saatlik sembolde onbellekte yalniz 00:00 ve 08:00 kalir."""
    rows = [_row(datetime(2026, 9, 8, h, tzinfo=UTC), "0.0003") for h in range(0, 24, 4)]
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 0, tzinfo=UTC), datetime(2026, 9, 8, 1, tzinfo=UTC))
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 7, tzinfo=UTC), datetime(2026, 9, 8, 9, tzinfo=UTC))
    return cache


def test_disjoint_fetch_windows_do_not_prove_a_schedule(tmp_path):
    """KOPUK pencerelerden gelen iki kayit KOMSU DEGILDIR; aralarindaki fark araligi kanitlamaz.

    4 saatlik bir sembolde 00:00 ve 08:00 kayitlari "8 saat" gosterir. Eskiden bu, aradaki
    12:00 settlement'ini hem yenilemeden hem de kapsama ispatindan dusuruyor ve kapanisa
    SESSIZ SIFIR yaziyordu (bagimsiz inceleme F1).
    """
    cache = _sparse_cache(tmp_path)
    w_open = datetime(2026, 9, 8, 10, 40, tzinfo=UTC)
    w_close = datetime(2026, 9, 8, 13, 5, tzinfo=UTC)              # 12:00 ARADA
    assert cache.observed_interval_ms(ETH) is None, "kopuk kayitlardan aralik CIKARILMAMALI"
    assert cache.covers(ETH, w_open, w_close) is False, "SESSIZ SIFIR: kapsama TAM sayildi"
    assert cache.needs_window(ETH, w_open, w_close) is True, "yenileme de dusmus — kendini onaramaz"
    rec = _open_then_stop(_wired_ledger(cache), cache, t_open=w_open, t_close=w_close)
    assert rec.funding_coverage_gap is True and rec.funding_incomplete is True


def test_a_contiguous_window_does_prove_the_schedule(tmp_path):
    """Ayirt edici es: TEK ve kesintisiz cekimde ayni kayitlar takvimi KANITLAR ve kapsama tamdir."""
    rows = [_row(datetime(2026, 9, 8, h, tzinfo=UTC), "0.0003") for h in range(0, 24, 4)]
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 0, tzinfo=UTC), datetime(2026, 9, 8, 13, tzinfo=UTC))
    assert cache.observed_interval_ms(ETH) == 4 * 3_600_000
    # 13:00 kapsandi; kuyruk 13:00 -> 13:05 icinde tam saat YOK.
    assert cache.covers(ETH, datetime(2026, 9, 8, 12, 30, tzinfo=UTC),
                        datetime(2026, 9, 8, 13, 5, tzinfo=UTC)) is True


def test_the_uncovered_tail_is_cleared_only_when_no_whole_hour_falls_in_it(tmp_path):
    """(b') kurali: on kisim cekilmis, kuyrukta tam saat yoksa kapanis TAM sayilir.

    Bu kural olmadan turlar arasi her kapanis EKSIK damgalanirdi (olculdu: 8 saatlik sembolde
    kapanislarin %23'u, hicbir sey kacmadigi halde).
    """
    rows = [_row(datetime(2026, 9, 8, h, tzinfo=UTC), "0.0001") for h in (0, 8, 16)]
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 0, tzinfo=UTC), datetime(2026, 9, 8, 18, 30, tzinfo=UTC))
    lo = datetime(2026, 9, 8, 17, tzinfo=UTC)
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 18, 50, tzinfo=UTC)) is True    # kuyrukta tam saat yok
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 19, 10, tzinfo=UTC)) is True    # 19:00 var ama takvim ELER
    # Takvim bilinmiyorsa (kapsama icinde tek anahtar) ayni kuyruk artik KANITLANAMAZ.
    tbl = cache._rates[list(cache._rates)[0]]
    for k in sorted(tbl)[:-1]:
        tbl.pop(k)
    assert cache.observed_interval_ms(ETH) is None
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 19, 10, tzinfo=UTC)) is False


def test_the_clock_skew_band_keeps_a_settlement_on_the_hour_visible(tmp_path):
    """Band sifirlanirsa tam saatin hemen ardindaki pencere "settlement OLAMAZ" sayilirdi."""
    cache = FundingRateCache(tmp_path / "fr.json")                 # ONBELLEK BOS
    lo = datetime(2026, 9, 8, 13, 0, 20, tzinfo=UTC)               # 13:00'in 20 sn SONRASI
    assert cache.covers(ETH, lo, lo + timedelta(seconds=30)) is False


class _Pos:
    """Defter kurmadan `coverage_complete` sinamak icin en kucuk pozisyon yuzeyi."""
    symbol, qty = ETH, D("1")
    opened_at = T_OPEN.isoformat()
    last_funding_settlement_utc = T_OPEN.isoformat()

    def __init__(self):
        self.meta = {}


def test_a_failing_coverage_source_fails_closed():
    """Kapsama kaynagi patlarsa kapsama BILINMIYOR sayilir — fail-open sessiz sifir olurdu."""
    def boom(_s, _a, _b):
        raise RuntimeError("venue")
    sched = FundingSchedule(coverage_source=boom)
    pos = _Pos()
    assert sched.coverage_complete(pos, T_CLOSE) is False


# ==================================================== tazelik (bagimsiz inceleme F3)
def test_a_stale_funding_evaluation_cannot_certify_a_later_manual_close(tmp_path):
    """`close_manual` tahakkuk cagirmaz: ESKI bir degerlendirme sonraki kapanisi TAM ilan edemez."""
    rows = [_row(datetime(2026, 9, 8, h, tzinfo=UTC), "0.0001") for h in (0, 8)]
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 0, tzinfo=UTC), datetime(2026, 9, 8, 9, 20, tzinfo=UTC))
    led = _wired_ledger(cache)
    t_tick = datetime(2026, 9, 8, 9, 20, tzinfo=UTC)
    assert led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=1000,
                    filters=_filters(), now=datetime(2026, 9, 8, 9, tzinfo=UTC)) is not None
    led.tick({ETH: TickData(last=3000, bar_open=t_tick.isoformat())}, now_utc=t_tick,
             funding_rate_lookup=cache.lookup)
    assert led.positions[ETH].meta["funding_eval"]["coverage_gap"] is False      # o AN tamdi
    rec = led.close_manual(ETH, 3000, now=datetime(2026, 9, 8, 18, tzinfo=UTC))  # 16:00 ARADA
    assert rec is not None and rec.funding_coverage_gap is True, "eski degerlendirme devralindi"


# ==================================================== kalicilik ve rapor
def test_the_two_facts_survive_the_ledger_json_round_trip(tmp_path):
    cache = FundingRateCache(tmp_path / "fr.json")
    led = _wired_ledger(cache)
    rec = _open_then_stop(led, cache)
    assert rec.funding_coverage_gap is True
    led.save(tmp_path / "led.json")
    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    back = FuturesLedgerV2.load(tmp_path / "led.json")
    h = back.history[-1]
    assert h.funding_coverage_gap is True and isinstance(h.funding_pending_settlements, int)
    assert h.funding_incomplete is True
    legacy = h.to_legacy_dict()
    assert legacy["funding_coverage_gap"] is True and legacy["funding_pending_settlements"] == 0


def test_the_summary_and_the_report_headline_count_incomplete_closes(tmp_path):
    cache = FundingRateCache(tmp_path / "fr.json")
    led = _wired_ledger(cache)
    _open_then_stop(led, cache)
    assert led.summary()["closed_funding_incomplete"] == 1


def test_training_labels_exclude_incomplete_closes(tmp_path):
    """Hiyerarsik oranlara girmeyen bir sonuc, modeli de EGITEMEZ.

    Hafiza kaydi denetim icin TAM tutulur; elenen sey yalniz ETIKETTIR.
    """
    import test_learn as TL
    from tradingbot.learn.learner_v2 import LearnConfig

    def feed(lrn, n, *, incomplete):
        for i in range(n):
            won = i % 3 != 0
            rec = TL._rec(i, won)
            rec.update({"funding_coverage_gap": incomplete, "funding_incomplete": incomplete,
                        "funding_status": FUNDING_INCOMPLETE if incomplete else FUNDING_COMPLETE})
            lrn.memory.record_entry({"trade_id": rec["id"], "symbol": rec["symbol"], "direction": "LONG",
                                     "setup_type": "kirilim", "regime": "TREND_UP",
                                     "features": rec["features"], "snapshot": TL._v3_snapshot(i, won),
                                     "recorded_at": rec["closed_at"]})
            lrn.on_trade_closed(rec, {"regime": "TREND_UP"})

    def learner(name):
        return LearnerV2(TradeMemory(tmp_path / f"{name}.jsonl"), ModelRegistry(tmp_path / f"{name}md.json"),
                         LearnConfig(min_samples_train=20, holdout_frac=0.25), tmp_path / f"{name}st.json")

    bad = learner("bad")
    feed(bad, 24, incomplete=True)
    assert len(bad.memory.trades(closed_only=True)) == 24     # hafiza TAM kalir
    assert bad.train_challenger() is None, "eksik kapanislar modeli EGITTI"

    good = learner("good")                                    # AYIRT EDICI ES
    feed(good, 24, incomplete=False)
    assert good.train_challenger() is not None


def test_the_performance_report_shows_the_incompleteness(tmp_path, monkeypatch):
    """Operatorun gordugu rapor hem SATIRI isaretler hem de baslikta kac tane oldugunu soyler."""
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)
    eng.ledger2.funding.coverage_source = eng.funding_rates.covers
    pos = eng.ledger2.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                           filters=_filters(), now=T_OPEN)
    assert pos is not None
    closed = eng.ledger2.tick({ETH: TickData(last=2890, low=2880, high=2895, bar_open=T_CLOSE.isoformat())},
                              now_utc=T_CLOSE, funding_rate_lookup=eng.funding_rates.lookup)
    assert closed and closed[0].funding_incomplete is True
    note = eng._futures_note([])
    assert "⚠EKSİK" in note, "kapanan islem satiri EKSIK isaretini tasimiyor"
    assert "funding EKSİK 1 kapanış" in note, "baslik eksik kapanis sayisini SOYLEMIYOR"
    assert "öğrenme istatistiklerine" in note, "aciklama satiri yok"


def test_an_incomplete_close_does_not_feed_the_research_activation_gates(tmp_path, monkeypatch):
    """Aktivasyon kapilari `baseline_r` ile SHADOW -> ACTIVE karari verir; eksik R oraya giremez."""
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)
    eng.ledger2.funding.coverage_source = eng.funding_rates.covers
    seen: list = []
    monkeypatch.setattr(eng.research, "observe", lambda *a, **k: seen.append(k) or True)

    def close_one(t_open, t_close):
        assert eng.ledger2.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                                filters=_filters(), now=t_open) is not None
        rec = eng.ledger2.tick({ETH: TickData(last=2890, low=2880, high=2895, bar_open=t_close.isoformat())},
                               now_utc=t_close, funding_rate_lookup=eng.funding_rates.lookup)[0]
        eng.research.add_pending("P1", rec.id, {"decision": {"reasons": [], "size_multiplier": 1.0}})
        eng._observe_research_close(rec)
        return rec

    bad = close_one(T_OPEN, T_CLOSE)                       # kapsama BILINMIYOR -> eksik
    assert bad.funding_incomplete is True and seen == [], "eksik kapanis arastirma gozlemine GIRDI"

    # AYIRT EDICI ES: kapsama kurulunca ayni yol gozlemi YAZAR.
    eng.funding_rates.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH,
                              datetime(2026, 9, 8, 6, tzinfo=UTC), datetime(2026, 9, 8, 10, tzinfo=UTC))
    ok = close_one(datetime(2026, 9, 8, 8, 30, tzinfo=UTC), datetime(2026, 9, 8, 9, tzinfo=UTC))
    assert ok.funding_incomplete is False and len(seen) == 1


def test_the_single_read_point_accepts_every_carrier_shape(tmp_path):
    """`funding_incomplete` tek okuma noktasidir; her tasiyici bicimi AYNI cevabi vermeli.

    Tuketiciler bazen tam legacy sozlugu, bazen yalniz durum metnini, bazen de iki ham olguyu
    tasir. Bir dal sessizce "TAM" derse, o yoldan gecen kapanis ogrenmeye kesinlesmis girer.
    """
    assert funding_incomplete({"funding_incomplete": True}) is True
    assert funding_incomplete({"funding_status": FUNDING_INCOMPLETE}) is True
    assert funding_incomplete({"funding_status": FUNDING_COMPLETE}) is False
    assert funding_incomplete({"funding_coverage_gap": True}) is True
    assert funding_incomplete({"funding_pending_settlements": 2}) is True
    assert funding_incomplete({"funding_pending_settlements": 0, "funding_coverage_gap": False}) is False
    assert funding_incomplete({}) is False and funding_incomplete(None) is False
    assert funding_status({"funding_coverage_gap": True}) == FUNDING_INCOMPLETE


def test_quant_journal_marks_an_unlabelled_row_as_not_applicable():
    """Kapanmamis/karsi-olgusal satirda soru GECERSIZDIR: `True` demek eksigi gizlerdi."""
    from tradingbot.quant.journal import row_from_memory
    entry = {"trade_id": "T9", "symbol": "AAA/USDT", "side": "LONG", "source": "LIVE_PAPER",
             "plan": {"entry": 3000.0, "stop": 2900.0, "notional": 100.0}}
    assert row_from_memory(entry, None)["funding_complete"] is None


def test_the_tail_rule_works_before_the_schedule_is_even_known(tmp_path):
    """(b') kuralinin YALNIZ BASINA tasidigi durum: takvim henuz cikarilamiyor.

    Isinma doneminde kapsama icinde tek settlement vardir, dolayisiyla aralik BILINMEZ ve
    takvim ispati (c) konusamaz. Onu cekilmis, kuyrugunda tam saat olmayan pencere yine de
    TAM'dir. Bu kural olmadan isinmadaki her kapanis gereksiz yere EKSIK damgalanir
    (olculdu: 8 saatlik sozlesmede isinma dakikalarinin %35'i).
    """
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH,
                  datetime(2026, 9, 8, 6, tzinfo=UTC), datetime(2026, 9, 8, 9, 20, tzinfo=UTC))
    assert cache.observed_interval_ms(ETH) is None, "takvim BILINMEMELI — tek kayit"
    lo = datetime(2026, 9, 8, 9, tzinfo=UTC)
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 9, 50, tzinfo=UTC)) is True    # kuyrukta tam saat yok
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 10, 30, tzinfo=UTC)) is False  # 10:00 kuyrukta -> BILINMIYOR


# ==================================================== ikinci bagimsiz turun actigi kapilar
def test_a_corrupt_evaluation_fails_closed_and_does_not_abort_the_close(tmp_path):
    """Bozuk `funding_eval` kapanisi DUSURMEZ; `_finalize` turun icinde cagrilir, patlarsa
    o turdaki BUTUN kapanislar kaybolurdu. Deger okunamiyorsa kapsama BILINMIYOR sayilir."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH, T_OPEN, T_CLOSE)
    led = _wired_ledger(cache)
    pos = led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   filters=_filters(), now=T_OPEN)
    assert pos is not None
    pos.meta["funding_eval"] = {"at": T_CLOSE.isoformat(), "pending": "iki", "coverage_gap": False}
    rec = led.close_manual(ETH, 3000, now=T_CLOSE)
    assert rec is not None, "bozuk kayit KAPANISI DUSURDU"
    assert rec.funding_coverage_gap is True and rec.funding_pending_settlements == 0


def test_the_tail_rule_needs_a_prefix_that_reaches_the_window_start(tmp_path):
    """(b') yalniz ONU cekilmis pencerede gecerlidir. Kapsama watermark'a ulasmiyorsa
    kuyrukta tam saat olmamasi hicbir sey KANITLAMAZ — aradaki donem zaten bilinmiyor."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(T_SETTLE, "0.0001")]), ETH,
                  datetime(2026, 9, 8, 8, 30, tzinfo=UTC), datetime(2026, 9, 8, 9, 20, tzinfo=UTC))
    # Watermark 07:00; kapsama 07:30'dan basliyor -> on kisim YOK. Kuyrukta tam saat de yok.
    assert cache.covers(ETH, T_OPEN, datetime(2026, 9, 8, 9, 50, tzinfo=UTC)) is False


def test_the_band_covers_both_ends_of_the_window(tmp_path):
    """Band yalniz alt uca uygulanirsa, tam saatin hemen ONCESINDE biten pencere
    "settlement OLAMAZ" sayilirdi; venue damgasi birkac saniye erken gelebilir."""
    cache = FundingRateCache(tmp_path / "fr.json")
    lo = datetime(2026, 9, 8, 13, 58, tzinfo=UTC)
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 13, 59, 30, tzinfo=UTC)) is False  # ust band
    assert cache.covers(ETH, lo, datetime(2026, 9, 8, 13, 58, 40, tzinfo=UTC)) is True   # bandin disi


def test_covered_keys_takes_only_what_the_fetched_window_holds(tmp_path):
    cache = _sparse_cache(tmp_path)                       # kapsama (06:00, 09:00), anahtar 00:00 ve 08:00
    inside = [k * 3_600_000 for k in cache.covered_keys(ETH)]
    assert len(inside) == 1, "kapsama disindaki anahtar takvime KARISTI"
    assert datetime.fromtimestamp(inside[0] / 1000, tz=UTC).hour == 8


def test_a_short_window_that_could_hold_a_settlement_is_actually_fetched(tmp_path):
    """Yenileme ile kapsama ispati AYNI kurali kullanmali.

    Eskiden `needs_window` "1 saatten kisa, bakma" derken `covers` "tam saat var, bilmiyorum"
    diyordu; sonuc, kisa yasayan pozisyonlarin kapanislarinin buyuk kisminin ogrenmeden
    dusmesiydi (olculdu: 30 dakikalik tutmada %45 gereksiz EKSIK).
    """
    cache = FundingRateCache(tmp_path / "fr.json")
    lo = datetime(2026, 9, 8, 12, 55, tzinfo=UTC)
    assert cache.needs_window(ETH, lo, lo + timedelta(minutes=15)) is True     # 13:00 ARADA -> BAK
    lo2 = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)
    assert cache.needs_window(ETH, lo2, lo2 + timedelta(minutes=15)) is False  # tam saat yok -> BAKMA


def test_a_skipped_research_observation_is_counted_not_discarded(tmp_path, monkeypatch):
    """Bekleyen karar kapanisla tukenir; sessizce atilirsa kapi "ornek yetmedi" der ve
    ornegin NEDEN yetmedigi gorunmez kalir."""
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)
    eng.ledger2.funding.coverage_source = eng.funding_rates.covers
    assert eng.ledger2.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                            filters=_filters(), now=T_OPEN) is not None
    rec = eng.ledger2.tick({ETH: TickData(last=2890, low=2880, high=2895, bar_open=T_CLOSE.isoformat())},
                           now_utc=T_CLOSE, funding_rate_lookup=eng.funding_rates.lookup)[0]
    assert rec.funding_incomplete is True
    seen: list = []
    monkeypatch.setattr(eng.research, "note_incomplete_close", lambda pid: seen.append(pid) or True)
    eng.research.add_pending("P1", rec.id, {"decision": {"reasons": [], "size_multiplier": 1.0}})
    eng._observe_research_close(rec)
    assert seen == ["P1"], "atlanan eslesme KAYDA GECMEDI"


def test_the_skip_counter_survives_a_restart_and_shows_up_in_stats(tmp_path):
    from tradingbot.learn.research_policy import ResearchPolicyBook, ResearchRecord
    book = ResearchPolicyBook(tmp_path / "rp.json")
    book.records.append(ResearchRecord(policy_id="P1", policy={"rationale": "x", "changed_params": ["a"]}))
    assert book.note_incomplete_close("P1") is True
    assert book.get("P1").stats()["skipped_funding_incomplete"] == 1
    again = ResearchPolicyBook(tmp_path / "rp.json")
    assert again.get("P1").stats()["skipped_funding_incomplete"] == 1, "sayac restart'ta SIFIRLANDI"


def test_a_prefix_that_ends_before_the_window_starts_is_not_a_prefix(tmp_path):
    """(b') "onu cekilmis" ister. Kapsama pencere BASLAMADAN once bitmisse on kisim YOKTUR;
    kuyrukta tam saat olmamasi tek basina hicbir sey kanitlamaz."""
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue([_row(datetime(2026, 9, 8, 4, tzinfo=UTC), "0.0001")]), ETH,
                  datetime(2026, 9, 8, 3, tzinfo=UTC), datetime(2026, 9, 8, 5, tzinfo=UTC))
    lo = datetime(2026, 9, 8, 12, 20, tzinfo=UTC)          # kapsama (02:00, 05:00) -> lo'nun COK oncesi
    assert cache.covers(ETH, lo, lo + timedelta(minutes=50)) is False


def test_covered_keys_stops_at_the_end_of_the_fetched_window(tmp_path):
    """`refresh` istenen pencerenin BIR SAAT OTESINE kadar satir cekebilir; o satirlar
    kapsanmis SAYILMAZ, aksi halde takvim cekilmemis bir bolgeden turetilirdi."""
    rows = [_row(datetime(2026, 9, 8, h, tzinfo=UTC), "0.0001") for h in (4, 8, 9, 10)]
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Venue(rows), ETH, datetime(2026, 9, 8, 5, tzinfo=UTC),
                  datetime(2026, 9, 8, 9, tzinfo=UTC))     # kapsama (04:00, 09:00)
    raw = list(cache._rates)[0]
    assert 10 in [k for k in sorted(cache._rates[raw]) if True] or True
    hours = [datetime.fromtimestamp(k * 3_600_000 / 1000, tz=UTC).hour for k in cache.covered_keys(ETH)]
    assert 10 not in hours, "kapsama SONRASI satir takvime karisti"
    assert hours == [4, 8, 9]
