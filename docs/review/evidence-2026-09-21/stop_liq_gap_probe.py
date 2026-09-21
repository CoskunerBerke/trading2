# -*- coding: utf-8 -*-
"""Stop / likidasyon sirasi — BOSLUK ve BAR ICI belirsizlik sondasi (2026-09-21, REVIEW-2026-09-21 §4).

Bu bir test DEGIL; incelemedeki acik maddenin sayisal kanitidir. Dosya yazmaz, ag kullanmaz.
Senaryo A, tests/test_liquidation_stop_order_v1.py::test_a_nearer_stop_fires_before_a_farther_liquidation
ile AYNI girdidir; test yalniz cikis sebebini ve R<0'i dogrular, dolum fiyatini ve zarari DOGRULAMAZ.

Kullanim (depo kokunden):  python docs/review/evidence-2026-09-21/stop_liq_gap_probe.py
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

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

ETH = "ETH/USDT"
T0 = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
FILT = SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                     min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def run(label: str, stop: float, make_tick) -> None:
    led = FuturesLedgerV2("1000", slippage=SlippageModel.zero(), liq_params=LiquidationParams(liq_fee_pct=D("0.5")))
    pos = led.open(ETH, "LONG", 3000, SizeSpec(200, AmountType.NOTIONAL, leverage=2), stop=stop, filters=FILT, now=T0)
    liq, margin = float(pos.liquidation_price), float(pos.isolated_margin)
    kw = make_tick(liq)
    rec = led.tick({ETH: TickData(**kw)}, now_utc=T0 + timedelta(hours=1))[0]
    print("%s\n   stop=%s liq=%.2f izole_marj=%.2f tick=%s\n   -> sebep=%s cikis=%.2f net_pnl=%.2f R=%.3f"
          % (label, stop, liq, margin, {k: round(v, 2) for k, v in kw.items()}, rec.exit_reason,
             float(rec.exit_price), float(rec.net_pnl), float(rec.r_multiple)))


def main() -> None:
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print("kod:", sha or "?")
    run("A) 2x LONG, stop 2400 (likidasyondan YAKIN); bar likidasyonun 50 altinda KAPANIYOR (test senaryo 1)",
        2400, lambda liq: {"last": liq - 50, "mark": liq - 50, "low": liq - 50})
    run("B) ayni pozisyon; bar ici dip likidasyonun 50 altinda ama kapanis 2450 (stopun USTUNDE)",
        2400, lambda liq: {"last": 2450.0, "mark": 2450.0, "low": liq - 50})
    run("C) karsilastirma: stop 100 (likidasyon YAKIN); ayni bar -> likidasyon yolu",
        100, lambda liq: {"last": liq - 50, "mark": liq - 50, "low": liq - 50})


if __name__ == "__main__":
    main()
