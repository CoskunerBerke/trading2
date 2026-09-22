# -*- coding: utf-8 -*-
"""F1/F2/F3 yeniden üretim sondası (2026-09-22, REVIEW-2026-09-22).

Bu bir test DEĞİL; önce/sonra karşılaştırması için gerçek modülleri (FuturesLedgerV2, apply_closed_bars_to_ledger,
PatternBook, FundingRates) çalıştıran sayısal kanıttır. Fiyatlar ve sağlayıcı SENTETİKTİR; gerçek piyasa ölçümü,
gerçek ağ gecikmesi veya kârlılık kanıtı DEĞİLDİR. Dosya yazmaz (yalnız geçici dizin), ağ kullanmaz.

Kullanım (depo kökünden):  python docs/review/evidence-2026-09-22/repro_three_findings.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tradingbot.accounting import (  # noqa: E402
    AmountType,
    FuturesLedgerV2,
    LiquidationParams,
    MarketType,
    SizeSpec,
    SlippageModel,
    SymbolFilters,
    TickData,
)
from tradingbot.strategy_paper import apply_closed_bars_to_ledger  # noqa: E402

UTC = timezone.utc
M15 = 15 * 60_000


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _filters(sym: str) -> SymbolFilters:
    return SymbolFilters(symbol=sym, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                         min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _rec(r) -> str:
    return "sebep=%s cikis=%s net=%.4f R=%.3f" % (r.exit_reason, r.exit_price, float(r.net_pnl), float(r.r_multiple))


# ---------------------------------------------------------------------------------------------------- F1
def f1() -> None:
    print("== F1: gecerli ama canli mark'tan uzak kapanmis bar")
    day = datetime(2026, 9, 21, tzinfo=UTC)
    t_open, t_bar = day.replace(hour=9, minute=59), day.replace(hour=10)

    def run(label, *, low, close, mark_at_apply, later_mark, open_=100.0):
        sym = "NEW/USDT"
        led = FuturesLedgerV2("100", slippage=SlippageModel.zero())
        led.open(sym, "LONG", 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=95, targets=[110],
                 filters=_filters(sym), now=t_open)
        row = {"timestamp": _ms(t_bar), "high": 103.0, "low": float(low), "close": float(close)}
        if open_ is not None:
            row["open"] = float(open_)
        spec = {"tf": "15m", "rows": [row], "mark": float(mark_at_apply), "market": "USDM_PERP", "first_bar_ms": row["timestamp"]}
        recs = apply_closed_bars_to_ledger(led, {sym: spec}, now=t_bar + timedelta(minutes=16))
        pos = led.positions.get(sym)
        gaps = (pos.meta.get("ohlc_gaps") or {}) if pos else {}
        print("  %s\n    10:16 bar uygulamasi: kapanan=%d acik=%s gap=%s" % (
            label, len(recs), pos is not None, {k: [g.get("reason") for g in v] for k, v in gaps.items()}))
        if pos is not None and later_mark is not None:
            recs = led.tick({sym: TickData(last=later_mark, mark=later_mark)}, now_utc=t_bar + timedelta(minutes=17))
        for r in (led.history or []):
            f = r.features or {}
            print("    kapanis: %s | yol_dogrulanmadi=%s" % (_rec(r), f.get("path_unverified")))

    run("A) bar close=70, 10:16 mark=101 (band disi), 10:17 mark=112", low=70, close=70, mark_at_apply=101, later_mark=112)
    run("K) kontrol: ayni bar, 10:16 mark=72 (band ici)", low=70, close=70, mark_at_apply=72, later_mark=None)
    run("W) eski fitil vakasi: low=70 close=101, mark=101", low=70, close=101, mark_at_apply=101, later_mark=None)
    run("U) ayni bar ACILISSIZ (dogrulanamaz), 10:16 mark=101, 10:17 mark=112", low=70, close=70, mark_at_apply=101,
        later_mark=112, open_=None)


# ---------------------------------------------------------------------------------------------------- F2
def f2() -> None:
    print("== F2: funding agi istegi ile koruyucu cikis")
    import test_pattern_trader_v1 as tp
    from tradingbot.pattern_trader.funding import FundingRates

    day = datetime.fromtimestamp(tp.T0 / 1000, tz=UTC)
    t_open, t_after = day.replace(hour=7, minute=59), day.replace(hour=8, minute=1)
    tp.CLOCK[0] = _ms(t_open)
    sym, raw = "LNG/USDT", "LNGUSDT"
    ex = tp._exinfo([(raw, "LNG", "USDT", "PERPETUAL", "TRADING", tp.T0 - 500 * tp.DAY)])
    p = tp._provider({}, ex, funding_rates={sym: {_ms(day.replace(hour=8)): 0.0001}}, funding_info=[])
    entered, release = threading.Event(), threading.Event()
    orig_hist = p.funding_history

    def blocking_history(*a, **k):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test: funding_history serbest birakilmadi")
        raise TimeoutError("mock: fundingRate zaman asimi")

    with tempfile.TemporaryDirectory() as td:
        cfg = tp._cfg(Path(td))
        book = tp._book_obj(cfg)
        fr = FundingRates(p, clock_ms=lambda: tp.CLOCK[0], rate_ttl_s=0.0)
        book.bind_funding(fr)
        fr.hours_for(sym) if not hasattr(fr, "refresh") else fr.refresh([])     # YALNIZ aralik tablosu once yuklenir (oran gecmisi DEGIL)
        pos = book.ledger.open(sym, "LONG", 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=95, targets=[110],
                               filters=_filters(sym), now=t_open)
        assert pos is not None, book.ledger.last_reject_reason
        p.funding_history = blocking_history
        tp.CLOCK[0] = _ms(t_after)
        done = threading.Event()
        out: dict = {}

        def run_tick():
            out["recs"] = book.tick({sym: TickData(last=94, mark=94)}, now=t_after, funding_rate_lookup=fr.lookup)
            done.set()

        bg = None
        if hasattr(fr, "refresh"):                        # onarimli surum: ag istegi ayri is parcaciginda
            bg = threading.Thread(target=lambda: fr.refresh([sym]), daemon=True)
            bg.start()
            entered.wait(5)
        th = threading.Thread(target=run_tick, daemon=True)
        th.start()
        finished_while_blocked = done.wait(2.0)
        print("  istek beklerken: saglayici_cagrildi=%s tick_bitti=%s acik=%s kapanan=%d kilit_alinabilir=%s" % (
            entered.is_set(), finished_while_blocked, sym in book.ledger.positions, len(book.ledger.history),
            book.lock.acquire(timeout=0.2) and (book.lock.release() or True)))
        release.set()
        th.join(5)
        if bg is not None:
            bg.join(5)
        for r in book.ledger.history:
            f = r.features or {}
            print("  istek dondukten sonra: %s | funding=%s settled_until=%s" % (_rec(r), r.funding, f.get("funding_settled_until")))
        p.funding_history = orig_hist
        if hasattr(book, "reconcile_funding") and book.ledger.history:
            tp.CLOCK[0] = _ms(t_after) + 5 * 60_000         # ag geri geldi: tarayici adimi (refresh + uzlastirma)
            w0 = float(book.ledger.wallet_balance)
            fr.refresh([sym])
            first = book.reconcile_funding(datetime.fromtimestamp(tp.CLOCK[0] / 1000, tz=UTC))
            again = book.reconcile_funding(datetime.fromtimestamp(tp.CLOCK[0] / 1000, tz=UTC))
            r = book.ledger.history[-1]
            f = r.features or {}
            print("  gec uzlastirma: islenen=%d ikinci_cagri=%d tutar=%s kayit_funding=%s kayit_net=%.4f cuzdan_degisimi=%.4f "
                  "kapsama_tam=%s" % (len(first), len(again), [x["amount"] for x in first], r.funding, float(r.net_pnl),
                                     float(book.ledger.wallet_balance) - w0, (f.get("funding_coverage") or {}).get("complete")))
    tp.CLOCK[0] = tp.T0


# ---------------------------------------------------------------------------------------------------- F3
def f3() -> None:
    print("== F3: stop / likidasyon sirasi (2x, giris 3000, notional 200)")
    sym = "ETH/USDT"
    t0 = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)

    def run(label, side, stop, tick_kw):
        led = FuturesLedgerV2("1000", slippage=SlippageModel.zero(), liq_params=LiquidationParams(liq_fee_pct=D("0.5")))
        pos = led.open(sym, side, 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=2), stop=stop, filters=_filters(sym), now=t0)
        liq, margin = float(pos.liquidation_price), float(pos.isolated_margin)
        kw = tick_kw(liq)
        if "open" in kw and "open" not in TickData.__dataclass_fields__:
            kw = {k: v for k, v in kw.items() if k != "open"}
            label += "  [TickData open alani YOK - acilis yok sayildi]"
        w0 = float(led.wallet_balance)
        recs = led.tick({sym: TickData(**kw)}, now_utc=t0 + timedelta(hours=1))
        r = recs[0] if recs else None
        print("  %s\n    liq=%.2f marj=%.2f tick=%s -> %s cuzdan_degisimi=%.4f" % (
            label, liq, margin, {k: round(v, 2) for k, v in kw.items()}, _rec(r) if r else "KAPANMADI",
            float(led.wallet_balance) - w0))

    run("A) LONG stop 2400; fiyat-yalniz tik likidasyonun 50 altinda", "LONG", 2400, lambda l: {"last": l - 50, "mark": l - 50})
    run("A-OHLC) LONG stop 2400; acilis likidasyonun 50 altinda (bosluk)", "LONG", 2400,
        lambda l: {"open": l - 50, "last": l - 50, "mark": l - 50, "high": l - 40, "low": l - 50})
    run("B) LONG stop 2400; acilis 2990, dip likidasyonun 50 altinda, kapanis 2450", "LONG", 2400,
        lambda l: {"open": 2990.0, "last": 2450.0, "mark": 2450.0, "high": 2995.0, "low": l - 50})
    run("B2) LONG stop 2400; acilis 2300 (stop alti, likidasyon ustu), kapanis 2450", "LONG", 2400,
        lambda l: {"open": 2300.0, "last": 2450.0, "mark": 2450.0, "high": 2460.0, "low": 2290.0})
    run("C) LONG stop 100 (likidasyon yakin); tik likidasyonun 50 altinda", "LONG", 100, lambda l: {"last": l - 50, "mark": l - 50})
    run("S-A) SHORT stop 3600; fiyat-yalniz tik likidasyonun 50 ustunde", "SHORT", 3600, lambda l: {"last": l + 50, "mark": l + 50})


def main() -> None:
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print("kod:", sha or "?")
    f1()
    f2()
    f3()


if __name__ == "__main__":
    main()
