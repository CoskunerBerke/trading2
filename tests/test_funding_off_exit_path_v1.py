# -*- coding: utf-8 -*-
"""FUNDING AĞ İSTEĞİ KORUYUCU ÇIKIŞ YOLUNDA DEĞİL (2026-09-22; REVIEW-2026-09-22 F2).

ÖLÇÜLEN KUSUR (e9ed196): `PatternScanner.exit_check` → `PatternBook.tick` (`with self.lock`) → `FuturesLedgerV2.tick`
→ `FundingSchedule.accrue`/`hours_for` → `FundingRates.lookup`/`hours_for` → `provider.funding_history`/`funding_info`.
Tahakkuk stop kontrolünden ÖNCE yapıldığı için bekleyen bir HTTP isteği stopu ve defter kilidini bekletiyordu:
07:59 LONG 100 / stop 95; 08:01 mark 94 → istek beklerken pozisyon açık, kapanış 0, kilit alınamıyor; istek
TimeoutError ile dönünce stop. Aralık tablosunu önceden ısıtmak `funding_history` beklemesini çözmüyordu.

ONARIM: `lookup`/`hours_for`/`settlement_mark` yalnız bellekten okur; ağ yalnız `FundingRates.refresh()`tedir ve onu
TARAYICI iş parçacığı (`PatternScanner.funding_step`, `scan_cycle` başında) çağırır — istek sürerken ne defter kilidi
ne `FundingRates` kilidi tutulur. Bilinmeyen dönem BEKLER (sıfır/tahmin yazılmaz). Pozisyon oran gelmeden kapanırsa
dönem kapanmış işleme sonradan, bir kez işlenir (`FuturesLedgerV2.settle_late_funding`; sözleşme docstring'de).

DETERMİNİZM: bekleme `threading.Event` ile kurulur; gerçek ağ ve uzun `sleep` yoktur. `Event.wait(timeout)` yalnız
BAŞARISIZ senaryoda (onarım geri alındığında) testin sonsuza kadar asılı kalmaması içindir. Bu bir sıralama/engellenme
kanıtıdır, gerçek ağ gecikmesi ölçümü DEĞİLDİR. Fiyatlar ve sağlayıcı SENTETİKTİR.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_pattern_trader_v1 as tp  # noqa: E402
from tradingbot.accounting import AmountType, MarketType, SizeSpec, SymbolFilters  # noqa: E402

UTC = timezone.utc
SYM, RAW = "LNG/USDT", "LNGUSDT"
DAY = datetime.fromtimestamp(tp.T0 / 1000, tz=UTC)
T_OPEN = DAY.replace(hour=7, minute=59)
T_SETTLE = DAY.replace(hour=8)
T_AFTER = DAY.replace(hour=8, minute=1)
RATE = 0.0001                                          # TEST DEĞERİ


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture(autouse=True)
def _clock():
    tp.CLOCK[0] = _ms(T_OPEN)
    yield
    tp.CLOCK[0] = tp.T0


class _Net:
    """Sağlayıcı ağ yüzeyi sayacı + isteğe bağlı Event bekletmesi."""

    def __init__(self, p):
        self.p, self.calls = p, {"history": 0, "info": 0}
        self.orig_hist, self.orig_info = p.funding_history, p.funding_info
        self.entered, self.release = threading.Event(), threading.Event()

    def count(self):
        def hist(*a, **k):
            self.calls["history"] += 1
            return self.orig_hist(*a, **k)

        def info(*a, **k):
            self.calls["info"] += 1
            return self.orig_info(*a, **k)
        self.p.funding_history, self.p.funding_info = hist, info

    def block(self, which: str):
        def blocked(*a, **k):
            self.calls[which] += 1
            self.entered.set()
            if not self.release.wait(10):
                raise TimeoutError("test: istek serbest bırakılmadı")
            raise TimeoutError("mock: zaman aşımı")
        if which == "history":
            self.p.funding_history = blocked
        else:
            self.p.funding_info = blocked


def _setup(tmp_path, *, side="LONG", intervals_loaded=True):
    sym_f = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                          min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)
    ex = tp._exinfo([(RAW, "LNG", "USDT", "PERPETUAL", "TRADING", tp.T0 - 500 * tp.DAY)])
    p = tp._provider({}, ex, funding_rates={SYM: {_ms(T_SETTLE): RATE}}, funding_info=[])
    cfg = tp._cfg(tmp_path)
    sc, book = tp._scanner(cfg, p)
    fr = sc.funding
    if intervals_loaded:
        (fr.refresh([]) if hasattr(fr, "refresh") else fr.hours_for(SYM))   # yalnız aralık tablosu, oran geçmişi DEĞİL
    stop, target = (95, 110) if side == "LONG" else (105, 90)
    w0 = book.ledger.wallet_balance
    pos = book.ledger.open(SYM, side, 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=stop, targets=[target],
                           filters=sym_f, now=T_OPEN)
    assert pos is not None, book.ledger.last_reject_reason
    tp.CLOCK[0] = _ms(T_AFTER)
    tp._set_mark(p, SYM, 94.0 if side == "LONG" else 106.0, ts=tp.CLOCK[0])
    return cfg, p, sc, book, fr, w0


def _exit_in_worker(sc):
    done, out = threading.Event(), {}

    def run():
        out["recs"] = sc.exit_check()
        done.set()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, done, out


# ============================================================ mimariden bağımsız: eski kodda da çağrılabilir
@pytest.mark.parametrize("which,intervals_loaded", [("history", True), ("info", False)])
def test_a_waiting_funding_request_does_not_hold_the_protective_stop(tmp_path, which, intervals_loaded):
    """`funding_history` (aralık tablosu yüklü) ve `funding_info` (tablo hiç yüklenmemiş) beklemeleri AYRI sınanır:
    çıkış izleyicisi, sağlayıcı isteği serbest bırakılmadan stopu işlemeli ve defter kilidini tutmamalı."""
    _cfg, p, sc, book, fr, _w0 = _setup(tmp_path, intervals_loaded=intervals_loaded)
    net = _Net(p)
    net.block(which)
    th, done, out = _exit_in_worker(sc)
    try:
        finished = done.wait(2.0)
        lock_free = book.lock.acquire(timeout=0.5)
        if lock_free:
            book.lock.release()
        assert finished, "çıkış izleyicisi funding ağ isteğini BEKLİYOR (stop gecikiyor)"
        assert lock_free, "defter kilidi ağ isteği boyunca tutuluyor"
        assert net.calls[which] == 0 and not net.entered.is_set(), "koruyucu çıkış yolunda ağa çıkıldı"
        assert len(out["recs"]) == 1 and out["recs"][0].exit_reason == "stop" and SYM not in book.ledger.positions
    finally:
        net.release.set()
        th.join(5)


def test_close_paths_make_no_hidden_funding_http_call(tmp_path):
    """Fiyat tiki, geçmiş bar uygulaması, zaman stopu (manuel kapanış) ve `_on_closed` kapsama hesabı sağlayıcıya
    HİÇ dokunmaz. (Piyasa fiyatını edinen ayrı veri yolu bu sayaca dahil DEĞİLDİR.)"""
    _cfg, p, sc, book, fr, _w0 = _setup(tmp_path)
    net = _Net(p)
    net.count()
    sc.exit_check()                                        # fiyat tiki → stop → _on_closed
    assert SYM not in book.ledger.positions
    # geçmiş bar yolu (ayrı pozisyon)
    sym_f = SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                          min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)
    book.ledger.open(SYM, "LONG", 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=95, filters=sym_f, now=T_AFTER)
    bar_t = T_AFTER.replace(minute=15)
    spec = {SYM: {"tf": "15m", "market": "USDM_PERP", "mark": 101.0, "first_bar_ms": _ms(bar_t),
                  "rows": [{"timestamp": _ms(bar_t), "open": 100.0, "high": 103.0, "low": 90.0, "close": 99.0}]}}
    recs = book.apply_closed_bars(spec, now=bar_t + timedelta(minutes=16), funding_rate_lookup=fr.lookup)
    assert len(recs) == 1 and recs[0].exit_reason == "stop"
    # zaman stopu (manuel kapanış yolu)
    book.ledger.open(SYM, "LONG", 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=95, filters=sym_f, now=T_AFTER)
    book.ledger.positions[SYM].meta["max_hold_bars"] = 1
    later = T_AFTER + timedelta(hours=2)
    book._time_stop(SYM, now=later, as_of_ms=_ms(later), price={"ok": True, "mark": 100.0, "price_ts_ms": _ms(later)})
    assert SYM not in book.ledger.positions and book.ledger.history[-1].exit_reason == "TIME_STOP"
    assert net.calls == {"history": 0, "info": 0}, net.calls


# ============================================================ onarımın mimarisi: ağ tarayıcıda, çıkış beklemez
def test_the_scanner_side_refresh_can_block_while_the_exit_monitor_still_closes(tmp_path):
    _cfg, p, sc, book, fr, _w0 = _setup(tmp_path)
    net = _Net(p)
    net.block("history")
    bg = threading.Thread(target=sc.funding_step, daemon=True)
    bg.start()
    try:
        assert net.entered.wait(5), "kurulum: tarayıcı adımı ağ isteğine girmeli"
        recs = sc.exit_check()                              # istek HÂLÂ bekliyor
        assert not net.release.is_set() and bg.is_alive()
        assert len(recs) == 1 and recs[0].exit_reason == "stop"
        assert fr.lookup(SYM, T_SETTLE) is None, "ağ isteği sürerken bellek okuması bekletilmemeli"
        cov = recs[0].features["funding_coverage"]
        assert cov["due"] == 1 and cov["settled"] == 0 and cov["complete"] is False, "bilinmeyen dönem sıfır YAZILMAZ"
        assert recs[0].funding == 0 and book.ledger.total_funding == 0
    finally:
        net.release.set()
        bg.join(5)


@pytest.mark.parametrize("side,sign", [("LONG", -1), ("SHORT", +1)])
def test_funding_that_arrives_after_the_close_is_posted_once_and_ledger_and_reports_agree(tmp_path, side, sign):
    cfg, p, sc, book, fr, w0 = _setup(tmp_path, side=side)
    recs = sc.exit_check()                                  # oran bellekte YOK → stop, 08:00 dönemi bekler
    assert len(recs) == 1 and recs[0].features["funding_coverage"]["complete"] is False
    rec = book.ledger.history[-1]
    net_before, gross, fees = rec.net_pnl, rec.gross_pnl, rec.fees
    tp.CLOCK[0] = _ms(T_AFTER) + 5 * 60_000
    out = sc.funding_step()                                 # ağ (tarayıcı) + bellekten uzlaştırma
    assert out.get("late_posted") == 1, out
    qty, mark = rec.quantity, D(str(p.marks[RAW]["mark"]))   # kaydın miktarı (kayma dahil) × settlement satırının mark'ı
    amount = D(sign) * qty * mark * D(str(RATE))
    assert rec.funding == amount and (rec.funding_paid if side == "LONG" else rec.funding_received) == abs(amount)
    assert rec.net_pnl == net_before + amount == gross - fees + rec.funding and rec.pnl == rec.net_pnl
    assert rec.r_multiple == rec.net_pnl / D(rec.features["risk_usdt"])
    assert book.ledger.wallet_balance - w0 == rec.net_pnl, "cüzdan ile işlem kaydı tutmalı"
    assert book.ledger.total_funding == sum((r.funding for r in book.ledger.history), D(0))
    cov = rec.features["funding_coverage"]
    assert cov["complete"] is True and cov["settled"] == 1 and len(rec.features["funding_late"]) == 1
    row = book.closed_recent[-1]
    assert row["funding_late"] is True and row["net_pnl"] == float(rec.net_pnl) and row["funding_complete"] is True
    assert book.reconcile_funding(datetime.fromtimestamp(tp.CLOCK[0] / 1000, tz=UTC)) == [], "aynı dönem ikinci kez işlenmez"
    wallet = book.ledger.wallet_balance
    book.save(None, datetime.fromtimestamp(tp.CLOCK[0] / 1000, tz=UTC))
    book2 = tp._book_obj(cfg)                               # YENİDEN BAŞLATMA: defter dosyadan
    book2.bind_funding(fr)
    assert book2.reconcile_funding(datetime.fromtimestamp(tp.CLOCK[0] / 1000, tz=UTC)) == []
    assert book2.ledger.wallet_balance == wallet and book2.ledger.history[-1].funding == amount
    assert len(book2.ledger.history[-1].features["funding_late"]) == 1
