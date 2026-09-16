# -*- coding: utf-8 -*-
"""T2/M2 PAPER — canlı fiyat ve zaman kusurları (3 bulgu); gerçek motor turu + gerçek StrategyBook/FuturesLedgerV2.
python repro_pt.py <repo> <workdir>
Ağ yok: tests/test_engine_v3._engine harness'ı (sentetik çerçeveler + sahte canlı snapshot). Sağlayıcı taklit edilir;
doğrulama yolu (_bind_provenance → verify_paper_data → apply_action / _paper_marks → ledger.tick) gerçek koddur.
F1 çıkış izleyicisi eski (bağlı) fiyatı tekrar kullanıyor mu?  F2 giriş öncesi 1h fitili stop kapanışı üretiyor mu?
F3 haftalarca eski günlük seri yeni tur kimliğiyle giriş verebiliyor mu?"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

REPO, WORK = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
os.environ["TRADINGBOT_CODE_SHA"] = "repro-pt"
import pytest  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import BTC, SYMS, _install  # noqa: E402

OV = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                      "chart_analysis": {"enabled": False}, "news": {"enabled": False}}      # news kapalı: venue olayı için ağa çıkılmaz
DAY = 86_400_000


def tag(cond, label):
    print(("  BULGU_VAR  " if cond else "  BULGU_YOK  ") + label)


def eng_for(root, mp):
    eng = _engine(root, mp, OV, symbols=2, equity=EQUITY)
    _force_triggers(mp, False)
    _install(eng, mp, btc_up=True, coin_above=True)
    return eng


def tour(eng):
    eng.tour(do_scan=False, obsidian=False, charts=False)


def books(eng):
    return {b.key: b for b in eng.strategy_books}


def positions(eng):
    return {k: sorted(b.ledger.positions) for k, b in books(eng).items()}


def ts(ms):
    return str(pd.Timestamp(int(ms), unit="ms", tz="UTC"))


def f1(root, mp):
    print("\n[F1] izleyici: sağlıklı tur (pozitif bağlı perp mark, 10 dk eski) → aynı run_id ile 3 exit-check; sağlayıcı stop altı fiyat veriyor")
    eng = eng_for(root, mp)
    tour(eng)                                                        # sağlıklı tur: güncel snapshot, pozitif bağlı perp_mark
    t2 = books(eng)["strategy_paper"]
    assert positions(eng)["strategy_paper"] == sorted(SYMS), positions(eng)
    stop = {s: float(t2.ledger.positions[s].stop) for s in SYMS}
    # 10 dakika geçmiş olsun: turun bağlı kaydı (ve sahte sağlayıcının kendi görüntüsü) 600 sn yaşlanır; yeni tur AÇILMAZ
    eng._fake_live._now_s = time.time() - 600.0
    for s in SYMS:
        pm = eng._frame_provenance[s].get("perp_mark") or {}
        for k, d in (("ts", 600.0), ("price_ts_ms", 600_000), ("fetched_at_ms", 600_000)):
            if pm.get(k) is not None:
                pm[k] = pm[k] - d
    bound = {s: (eng._frame_provenance[s].get("perp_mark") or {}) for s in SYMS}
    print("   bağlı perp_mark (10 dk eski):", {s: (round(float(b.get("price") or 0), 4), b.get("ts")) for s, b in bound.items()})
    calls = []
    orig = eng.runner.live.snapshot

    def fresh_low(sym):
        calls.append(sym)
        d = dict(orig(sym)); d["funding"] = {"rate": 0.0, "mark": stop[sym] * 0.95}; d["ts"] = time.time()
        return d
    mp.setattr(eng.runner.live, "snapshot", fresh_low)
    ticks = []
    for _ in range(3):
        eng._strategy_paper_exit_check()
        ticks.append({s: round(float(t2.ledger.positions[s].last_price), 4) if s in t2.ledger.positions else None for s in SYMS})
    print(f"   run_id aynı; sağlayıcı çağrısı={len(calls)} tick fiyatları={ticks} stop={ {s: round(v, 4) for s, v in stop.items()} }")
    print(f"   pozisyon={positions(eng)} kapanış={ {k: b.counters['closed'] for k, b in books(eng).items()} }")
    tag(len(calls) == 0 and positions(eng)["strategy_paper"] == sorted(SYMS), "izleyici canlı sağlayıcıyı hiç sormadı; eski (tur) fiyatıyla pozisyon stop altında açık kaldı")
    for s in SYMS:                                                   # pozitif kontrol: bağlı mark kaldırılınca sağlayıcı sorulur, stop çalışır
        eng._frame_provenance[s]["perp_mark"] = None
    n0 = len(calls)
    eng._strategy_paper_exit_check()
    print(f"   bağlı mark kaldırılınca: sağlayıcı çağrısı +{len(calls) - n0}, pozisyon={positions(eng)}")


def f2(root, mp):
    print("\n[F2] giriş öncesi kapanmış 1h mumunun low'u stop altında; pozisyon sonra açılıyor; güncel mark = giriş")
    eng = eng_for(root / "a", mp)                                    # (a) tur içi yol: tur pozisyonu açar ve hemen tick'ler
    orig = eng.runner.run_symbol

    def wick(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        fr = dict(eng.runner.last_frames[symbol]); h1 = fr["1h"].copy()
        px = float(b.price)
        h1.loc[h1.index[-1], "low"] = px * 0.88                        # stop (~%9 altı) altında, ±%20 süzgeci içinde
        h1.loc[h1.index[-1], "high"] = px * 1.01                       # sentetik 1h serisi bağımsız seviyede: uçları fiyata hizala
        fr["1h"] = h1; eng.runner.last_frames[symbol] = fr
        return b
    mp.setattr(eng.runner, "run_symbol", wick)
    tour(eng)
    b = books(eng)["strategy_paper"]
    last_open = int(eng.runner.last_frames[SYMS[0]]["1h"]["timestamp"].iloc[-1])
    opened_at = [p.opened_at for p in b.ledger.positions.values()] or [h.get("opened_at") for h in b.ledger.history_dicts()]
    print(f"   son 1h bar açılış={ts(last_open)} kapanış={ts(last_open + 3_600_000)}; pozisyon açılışı={opened_at[:1]}")
    print(f"   açılan={b.counters['opened']} kapanan={b.counters['closed']} pozisyon={positions(eng)['strategy_paper']} nedenler={[(c['symbol'], c['exit_reason']) for c in b.closed_recent]}")
    tag(b.counters["opened"] == len(SYMS) and b.counters["closed"] == len(SYMS), "tur tick'i: pozisyon açıldığı turda giriş ÖNCESİ bar fitiliyle stop'landı")
    eng2 = eng_for(root / "b", mp)                                   # (b) izleyici yolu: pozisyon normal açıldı; sonra aynı eski barın low'u düşük
    tour(eng2)
    b2 = books(eng2)["strategy_paper"]
    assert positions(eng2)["strategy_paper"] == sorted(SYMS)
    for s in SYMS:
        h1 = eng2.runner.last_frames[s]["1h"]
        h1.loc[h1.index[-1], "low"] = float(b2.ledger.positions[s].entry_avg) * 0.88
        h1.loc[h1.index[-1], "high"] = float(b2.ledger.positions[s].entry_avg) * 1.01
    mae_before = {s: float(b2.ledger.positions[s].mae_pct) for s in SYMS}
    eng2._strategy_paper_exit_check()
    print(f"   izleyici: pozisyon={positions(eng2)['strategy_paper']} kapanan={b2.counters['closed']} nedenler={[(c['symbol'], c['exit_reason']) for c in b2.closed_recent]} mae_önce={mae_before}")
    tag(b2.counters["closed"] == len(SYMS), "60 sn izleyici: giriş öncesi 1h fitili yeni fiyat hareketi gibi kullanıldı, stop kapanışı üretti")


def f3(root, mp):
    print("\n[F3] coin + BTC günlük serisi 46 gün eski (yeterli sayıda mum), yeni tur kimliğiyle bağlanıyor")
    eng = eng_for(root, mp)
    orig = eng.runner.run_symbol

    def old_daily(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        for s in (symbol, BTC):
            fr = dict(eng.runner.last_frames[s]); d1 = fr["1d"].copy()
            d1["timestamp"] = d1["timestamp"] - 46 * DAY
            d1.index = d1.index - pd.Timedelta(days=46)
            fr["1d"] = d1; eng.runner.last_frames[s] = fr
        eng._bind_provenance(BTC)                                    # BTC bağı motorun yaptığı gibi YÜKLÜ (eski) çerçeveye; coin bağı motorda
        return b
    mp.setattr(eng.runner, "run_symbol", old_daily)
    tour(eng)
    b = books(eng)["strategy_paper"]
    last = int(eng.runner.last_frames[SYMS[0]]["1d"]["timestamp"].iloc[-1])
    print(f"   karar anı={ts(time.time() * 1000)} serinin son barı açılış={ts(last)} (kapanışı +1g)")
    print(f"   data_checks={ {s: {k: (b.data_checks.get(s) or {}).get(k) for k in ('ok', 'entry_ok', 'reason')} for s in SYMS} }")
    print(f"   ret={b.rejections} açılan={b.counters['opened']} pozisyon={positions(eng)['strategy_paper']}")
    ds = {s: (b.ledger.positions[s].features.get("data_source") or {}).get("bars") for s in SYMS if s in b.ledger.positions}
    print(f"   data_source.bars={ds}")
    tag(b.counters["opened"] == len(SYMS), "46 gün eski günlük seriyle (yeni tour_id) veri kapısı geçti ve T2 giriş açtı")


if __name__ == "__main__":
    for name, fn in (("f1", f1), ("f2", f2), ("f3", f3)):
        mp = pytest.MonkeyPatch()
        try:
            fn(WORK / name, mp)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            mp.undo()
