# -*- coding: utf-8 -*-
"""Kalan üç bulgunun yeniden üretimi — gerçek FastAPI (TestClient), gerçek SpotLedger, gerçek motor turu (test harness).
python repro3.py <repo> <workdir>"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO, WORK = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
os.environ["TRADINGBOT_CODE_SHA"] = "repro3-sha"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.accounting.spot_ledger import SpotLedger  # noqa: E402
from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402
from tradingbot.timeframes import TF_MS  # noqa: E402

NOW = int(time.time() * 1000)


def series(n, tf, seed, start):
    step = TF_MS[tf]; last_open = (NOW // step) * step
    ts = last_open - np.arange(n)[::-1] * step
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"timestamp": ts, "open": close * 0.999, "high": close * 1.006, "low": close * 0.994, "close": close, "volume": 5.0})


def env(root: Path, *, fut_pos: dict | None = None):
    if root.exists():
        shutil.rmtree(root)
    st, data = root / "state", root / "data"; st.mkdir(parents=True); data.mkdir()
    for tf, n in (("4h", 500), ("1h", 500), ("1d", 420)):
        series(n, tf, 1, 100.0).to_csv(data / f"tv-binance_BTC-USDT_{tf}.csv", index=False)
        series(n, tf, 2, 200.0).to_csv(data / f"binanceusdm_BTC-USDT_{tf}.csv", index=False)
    (st / "coin_heads.json").write_text(json.dumps({"generated_at": "2026-09-16T00:00:00+00:00", "run_id": "r1", "heads": [], "chief": {}}), encoding="utf-8")
    (st / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "positions": ({"BTC/USDT": fut_pos} if fut_pos else {}), "history": []}), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0, "positions": {}, "history": []}), encoding="utf-8")
    for fn, d in (("killswitch.json", {"state": "ARMED", "since": "", "reasons": [], "audit": []}), ("mode.json", {"mode": "PAPER", "history": []}),
                  ("risk.json", {"profile": {"name": "PAPER_RESEARCH"}, "exposure": {"equity": 100}, "last_decisions": []})):
        (st / fn).write_text(json.dumps(d), encoding="utf-8")
    return st, data


def kinds(j):
    return {e["kind"]: e for e in ((j.get("analysis") or {}).get("elements") or [])}


def tag(cond, label):
    print(("  BULGU_VAR  " if cond else "  BULGU_YOK  ") + label)


def f1(root):
    print("\n[1] spot defteri değişimi önbelleği geçersizleştirmiyor mu?")
    st, data = env(root / "f1")
    led = SpotLedger(starting_cash=Decimal("1000"))
    o = led.market_buy("BTC/USDT", qty=Decimal("2"), ref_price=Decimal("100"), now=datetime.now(timezone.utc))
    led.save(st / "spot_ledger.json")
    print(f"   alış emri durumu={getattr(o, 'status', None)} pozisyon={led.positions().get('BTC/USDT', {}).get('units')}")
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    j1 = c.get("/api/chart/BTC?tf=4h&market=spot&book=main&n=100").json()
    print(f"   1. istek: origin={j1.get('analysis_origin')} live.source={(j1.get('live') or {}).get('source')} analysis.position.qty={(j1['analysis'].get('position') or {}).get('qty')} entry={'var' if 'entry' in kinds(j1) else 'yok'}")
    led = SpotLedger.load(st / "spot_ledger.json")
    led.market_sell("BTC/USDT", qty=Decimal("2"), ref_price=Decimal("105"), now=datetime.now(timezone.utc))
    led.save(st / "spot_ledger.json")                      # yalnız spot_ledger.json değişti
    print(f"   satış sonrası StateReader.spot_positions()={StateReader(st).spot_positions()}")
    j2 = c.get("/api/chart/BTC?tf=4h&market=spot&book=main&n=100").json()
    a2 = j2.get("analysis") or {}
    print(f"   2. istek: origin={j2.get('analysis_origin')} live.position={(j2.get('live') or {}).get('position')} payload.position={j2.get('position')} "
          f"analysis.position.qty={(a2.get('position') or {}).get('qty')} entry={'var' if 'entry' in kinds(j2) else 'yok'} aynı analysis_id={a2.get('analysis_id') == j1['analysis']['analysis_id']} live.source={(j2.get('live') or {}).get('source')}")
    tag((a2.get("position") or {}).get("qty") == 2 or "entry" in kinds(j2), "tam satış sonrası analiz hâlâ 2 birim açık pozisyon çiziyor")
    tag((j2.get("live") or {}).get("source") == "portfolio.json", "live.source spot_ledger.json kullanılırken portfolio.json diyor")


def f2(root):
    print("\n[2] geçici analizde canlı fiyat/K-Z donuyor mu?")
    pos = {"id": "F00007", "symbol": "BTC/USDT", "side": "LONG", "qty": "2", "entry_avg": "200", "stop": "190", "targets": [], "opened_at": "2026-09-15T00:00:00+00:00", "leverage": 1}
    st, data = env(root / "f2", fut_pos=pos)
    for tf in ("4h", "1h"):
        p = data / f"binanceusdm_BTC-USDT_{tf}.csv"; df = pd.read_csv(p)
        df.loc[df.index[-1], "close"] = 201.0; df.loc[df.index[-1], "high"] = 202.0; df.loc[df.index[-1], "open"] = 200.5; df.loc[df.index[-1], "low"] = 199.0
        df.to_csv(p, index=False)
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    for tf in ("4h", "1h"):
        j1 = c.get(f"/api/chart/BTC?tf={tf}&market=futures&book=main&n=100").json()
        m1 = kinds(j1).get("mark") or {}
        print(f"   {tf} 1. istek: origin={j1.get('analysis_origin')} live.mark={(j1.get('live') or {}).get('mark_price')} mark.price={m1.get('price')} label={m1.get('label_tr')}")
        p = data / f"binanceusdm_BTC-USDT_{tf}.csv"; df = pd.read_csv(p)
        df.loc[df.index[-1], "close"] = 240.0; df.loc[df.index[-1], "high"] = 241.0
        df.to_csv(p, index=False)                          # yalnız açık mumun kapanışı değişti
        j2 = c.get(f"/api/chart/BTC?tf={tf}&market=futures&book=main&n=100").json()
        m2 = kinds(j2).get("mark") or {}
        print(f"   {tf} 2. istek: origin={j2.get('analysis_origin')} stored={j2.get('analysis_stored')} live.mark={(j2.get('live') or {}).get('mark_price')} son mum close={j2['c'][-1]} mark.price={m2.get('price')} label={m2.get('label_tr')}")
        tag(m2.get("price") != 240.0, f"{tf}: çizilen işaret fiyatı 240 değil (donmuş)")


def f3(root):
    print("\n[3] T2/M2 kaydı spot mumları futures olarak etiketliyor mu? (gerçek motor turu, SPOT provenans)")
    import pytest
    from test_engine_v3 import _engine
    from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile
    from test_strategy_paper_engine_v1 import _install
    from tradingbot.chart_analysis_store import ChartAnalysisStore
    mp = pytest.MonkeyPatch()
    ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                          "chart_analysis": {"enabled": True, "keep_per_series": 20}}
    eng = _engine(root / "f3", mp, ov, symbols=2, equity=EQUITY)
    _force_triggers(mp, False)
    _install(eng, mp, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    print(f"   provenance={ {k: v.get('market') for k, v in eng._frame_provenance.items()} }")
    store = ChartAnalysisStore(st)
    found = 0
    for key, rows in store.index()["series"].items():
        rec = store.load(rows[-1]["analysis_id"]); i = rec["identity"]
        print(f"   {key}: identity.market_type={i['market_type']} source.bar_market={rec['source'].get('bar_market')} last_close={i['last_closed_bar']['close']}")
        if i["book_id"] != "main" and i["market_type"] == "USDM_PERP" and rec["source"].get("bar_market") == "SPOT":
            found += 1
    cfg = json.loads((st / "chart_analysis" / "config.json").read_text(encoding="utf-8"))
    print(f"   config.json skipped={cfg.get('skipped')}")
    print(f"   T2 pozisyonları (işlem yolu, değişmemeli)={sorted(eng.strategy_books[0].ledger.positions)}")
    tag(found > 0, f"{found} T2/M2 kaydı SPOT mumlarını USDM_PERP kimliğiyle taşıyor")
    mp.undo()


if __name__ == "__main__":
    for fn in (f1, f2, f3):
        try:
            fn(WORK)
        except Exception:  # noqa: BLE001
            print("  !! betik hatası:"); traceback.print_exc()
