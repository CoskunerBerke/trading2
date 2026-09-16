# -*- coding: utf-8 -*-
"""T2/M2 işlem yolu veri kaynağı kusuru — gerçek motor turu + gerçek StrategyBook/defter (ağsız test harness'ı).
python repro_trade.py <repo> <workdir>
Senaryolar: S1 SPOT-only çerçeve; S2 coin USDM_PERP + BTC provenansı yok; S4 eski (bayat) onay + başarısız run_symbol;
S5 açık pozisyon + SPOT 1h fitili stop altında + perp mark stop üstünde."""
from __future__ import annotations

import json
import os
import sys
import traceback
from decimal import Decimal
from pathlib import Path

REPO, WORK = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
os.environ["TRADINGBOT_CODE_SHA"] = "repro-trade"
import pytest  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import SYMS, _install  # noqa: E402

OV = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                      "chart_analysis": {"enabled": False}}


def tag(cond, label):
    print(("  BULGU_VAR  " if cond else "  BULGU_YOK  ") + label)


def declare(eng, symbol, market):
    """Provenansı test adına ilan et (motorun kaydını ezer) ve varsa bağla (onarım sonrası API)."""
    eng._frame_provenance[symbol] = {"market": market, "source": "repro:%s" % market, "entry_ok": market == "USDM_PERP"}
    b = getattr(eng, "_bind_provenance", None)
    if callable(b):
        b(symbol)


def wrap(eng, mp, *, coin_market, btc_market):
    orig = eng.runner.run_symbol

    def w(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        if coin_market:
            declare(eng, symbol, coin_market)
        if btc_market:
            declare(eng, "BTC/USDT", btc_market)
        return b
    mp.setattr(eng.runner, "run_symbol", w)


def books(eng):
    return {b.key: (sorted(b.ledger.positions), dict(b.counters), {k: v for k, v in b.rejections.items()}) for b in eng.strategy_books}


def scenario(name, root, *, coin_market, btc_market, tours=1, before_tour2=None):
    mp = pytest.MonkeyPatch()
    eng = _engine(root, mp, OV, symbols=2, equity=EQUITY)
    _force_triggers(mp, False)
    try:
        _install(eng, mp, btc_up=True, coin_above=True, market=None, btc_market=None)   # onarim sonrasi imza: ilan YOK (motorun karari)
    except TypeError:
        _install(eng, mp, btc_up=True, coin_above=True)                                  # 25adb9d imzasi
    wrap(eng, mp, coin_market=coin_market, btc_market=btc_market)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    out = [books(eng)]
    if tours > 1:
        if before_tour2:
            before_tour2(eng, mp)
        eng.tour(do_scan=False, obsidian=False, charts=False)
        out.append(books(eng))
    prov = {k: v.get("market") for k, v in eng._frame_provenance.items()}
    print(f"\n[{name}] provenance={prov}")
    for i, o in enumerate(out, 1):
        for key, (pos, cnt, rej) in o.items():
            print(f"   tur{i} {key}: pozisyon={pos} sayaç={cnt} ret={rej}")
    mp.undo()
    return eng, out


if __name__ == "__main__":
    try:
        eng, out = scenario("S1 SPOT-only çerçeve (perp yok)", WORK / "s1", coin_market=None, btc_market=None)
        tag(any(o[0] for o in out[0].values()), "SPOT çerçeveyle T2/M2 futures pozisyonu açıldı")
        eng, out = scenario("S2 coin USDM_PERP, BTC provenansı YOK", WORK / "s2", coin_market="USDM_PERP", btc_market=None)
        tag(any(o[0] for o in out[0].values()), "BTC referansı doğrulanmadan yeni giriş açıldı")

        def fail_run_symbol_and_close(eng, mp):
            # tur 1'de doğrulanmış giriş; pozisyonu elle kapat; tur 2'de indirme başarısız → eski çerçeve + eski onay
            for b in eng.strategy_books:
                for s in list(b.ledger.positions):
                    b.ledger.close_manual(s, float(b.ledger.positions[s].entry_avg), reason="REPRO_CLOSE", now=__import__("tradingbot.core", fromlist=["utc_now"]).utc_now())
            def boom(symbol, analysis=None, prefetched=None):
                raise RuntimeError("download failed")
            mp.setattr(eng.runner, "run_symbol", boom)
        eng, out = scenario("S4 tur1 doğrulanmış, tur2 run_symbol başarısız (bayat çerçeve/onay)", WORK / "s4", coin_market="USDM_PERP", btc_market="USDM_PERP", tours=2, before_tour2=fail_run_symbol_and_close)
        tag(any(o[0] for o in out[1].values()), "bayat çerçeveyle (bu turda provenans yok) yeniden giriş açıldı")

        def spot_wick(eng, mp):
            # tur 2: perp alınamıyor (SPOT ikamesi) ve SPOT 1h fitili stop'un çok altında; perp mark ise stop üstünde
            orig = eng.runner.run_symbol
            def w(symbol, analysis=None, prefetched=None):
                b = orig(symbol, analysis, prefetched)
                eng._frame_provenance[symbol] = {"market": "SPOT", "source": "tradingview:BINANCE", "entry_ok": False, "reason": "FUTURES_FRAMES_UNAVAILABLE"}
                fr = dict(eng.runner.last_frames[symbol]); h1 = fr["1h"].copy()
                px = float(b.price)
                h1.loc[h1.index[-1], "low"] = px * 0.85                              # spot fitili: stop'un (≈-9%) altı, ±20% süzgecinin içi
                h1.loc[h1.index[-1], "high"] = max(float(h1["high"].iloc[-1]), px * 1.01)
                fr["1h"] = h1; eng.runner.last_frames[symbol] = fr
                return b
            mp.setattr(eng.runner, "run_symbol", w)
        eng, out = scenario("S5 açık pozisyon + SPOT 1h fitili stop altında (perp mark stop üstünde)", WORK / "s5", coin_market="USDM_PERP", btc_market="USDM_PERP", tours=2, before_tour2=spot_wick)
        closed = {k: v[1].get("closed") for k, v in out[1].items()}
        print(f"   tur2 kapanan={closed} closed_recent={[ (b.key, [(c['symbol'], c['exit_reason']) for c in b.closed_recent]) for b in eng.strategy_books]}")
        tag(any(v for v in closed.values()), "SPOT fitili futures pozisyonunu stop ile kapattı")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
