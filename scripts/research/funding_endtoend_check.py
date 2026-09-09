#!/usr/bin/env python3
"""URETIM YOLU ile BAGIMSIZ MUTABAKATIN ayni sonucu verdiginin kaniti (ag gerektirir).

`scripts/research/funding_recon.py` defterin kuralini SIFIRDAN yeniden yazarak gercek Binance
funding gecmisiyle mutabakat kurar. Bu betik ise URETIM kodunu (`FundingRateCache` +
`FundingSchedule.accrue` + `chained_rates`) gercek venue verisiyle calistirir. Iki bagimsiz
uygulama ayni sayiyi vermezse onarim dogrulanmis SAYILMAZ.

Kullanim:  python scripts/research/funding_endtoend_check.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tradingbot.accounting.funding import FundingSchedule, chained_rates, static_rates  # noqa: E402
from tradingbot.accounting.models import MarketType, Position, PositionSide  # noqa: E402
from tradingbot.market.funding_rates import FundingRateCache  # noqa: E402

SNAP = (r"C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/"
        r"259cbfe4-0b60-466a-b8f2-d5652499b5ef/snapshot")
#: `funding_recon.py`'nin bagimsiz olarak buldugu gercek degerler.
EXPECTED = {"F00015": +0.040753, "F00004": +0.008606, "F00034": -0.022832}
RAW = {"F00015": "STXUSDT", "F00004": "BZUSDT", "F00034": "NVDAUSDT"}


def _venue_rows(raw: str, lo: datetime, hi: datetime) -> list[dict]:
    url = ("https://fapi.binance.com/fapi/v1/fundingRate?"
           f"symbol={raw}&startTime={int((lo - timedelta(hours=9)).timestamp() * 1000)}"
           f"&endTime={int((hi + timedelta(hours=9)).timestamp() * 1000)}&limit=1000")
    rows = json.loads(urllib.request.urlopen(url, timeout=25).read().decode())
    # URETIM saglayicisinin cikti sekli (market/providers.py `funding_history`).
    return [{"symbol": r.get("symbol"), "funding_ts": int(r["fundingTime"]),
             "rate": float(r["fundingRate"]), "mark": float(r["markPrice"])} for r in rows]


def main() -> int:
    led = json.load(io.open(SNAP + "/futures_ledger.json", encoding="utf-8"))
    bad = 0
    print(f"{'id':7} {'symbol':13} {'side':5} {'due':>4} {'OLD':>11} {'NEW':>11} {'BEKLENEN':>11} {'LEDGER':>11}")
    for tid, raw in RAW.items():
        t = next(x for x in led["history"] if x["id"] == tid)
        o, c = datetime.fromisoformat(t["opened_at"]), datetime.fromisoformat(t["closed_at"])
        qty = Decimal(str(t["quantity"]))
        prov_rows = _venue_rows(raw, o, c)

        class _P:
            def funding_history(self, symbol, limit=100, start_ms=None, end_ms=None):
                return [r for r in prov_rows
                        if (start_ms is None or r["funding_ts"] >= start_ms)
                        and (end_ms is None or r["funding_ts"] <= end_ms)]

        side = PositionSide.LONG if t["side"] == "LONG" else PositionSide.SHORT

        def _mk():
            return Position(id=tid, symbol=t["symbol"], market_type=MarketType.USDM_PERP, side=side,
                            qty=qty, entry_avg=Decimal(str(t["entry"])), opened_at=t["opened_at"],
                            last_funding_settlement_utc=t["opened_at"])

        tmp = Path(tempfile.mkdtemp())
        try:
            cache, sch = FundingRateCache(tmp / "fr.json"), FundingSchedule()
            p = _mk()
            due = sch.settlements_due(p, c)
            cache.ensure(lambda: _P(), {t["symbol"]: due}, now=time.time())
            new = sum(e.amount for e in sch.accrue(p, c, Decimal(str(t["exit_price"])),
                                                   chained_rates(cache.lookup, static_rates({}))))
            old = sum(e.amount for e in sch.accrue(_mk(), c, Decimal(str(t["exit_price"])),
                                                   static_rates({t["symbol"]: Decimal(str(prov_rows[-1]["rate"]))})))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ok = abs(float(new) - EXPECTED[tid]) < 1e-6
        bad += 0 if ok else 1
        print(f"{tid:7} {t['symbol']:13} {t['side']:5} {len(due):4d} {float(old):+11.6f} {float(new):+11.6f} "
              f"{EXPECTED[tid]:+11.6f} {float(t['funding']):+11.6f}  {'OK' if ok else 'UYUSMUYOR'}")
    print("\n" + ("URETIM YOLU ile BAGIMSIZ MUTABAKAT 6 hanede AYNI." if not bad
                  else f"{bad} islemde UYUSMAZLIK — onarim dogrulanmadi."))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
