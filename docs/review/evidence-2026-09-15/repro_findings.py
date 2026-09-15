# -*- coding: utf-8 -*-
"""Altı bulgunun GERÇEK FastAPI uygulaması (TestClient) üzerinde yeniden üretimi — önce/sonra kanıtı.

Kullanım:  python repro_findings.py <repo_kökü> <çalışma_dizini>
Çıktı: her bulgu için ölçülen değerler; 'BULGU_VAR' / 'BULGU_YOK' etiketi.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(REPO))
os.environ["TRADINGBOT_CODE_SHA"] = "repro-sha"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.chart_analysis import build_snapshot, closed_bars_at, pivots  # noqa: E402
from tradingbot.chart_analysis_store import ChartAnalysisStore  # noqa: E402
from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402

H4 = 14_400_000
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": H4, "1d": 86_400_000, "1w": 604_800_000}
NOW = int(time.time() * 1000)
GATES = {"candle_mode": "ENFORCE", "candle_variant": "c3_4h_veto", "chart_mode": "SHADOW", "chart_variant": "p2_4h_veto",
         "regime_mode": "ENFORCE", "regime_variant": "r1_long_only_uptrend", "chart_fresh_within": 3}


def series(n: int, tf: str, seed: int, start: float) -> pd.DataFrame:
    """Son bar 'şimdi' açık (kapanmamış); ondan önceki n-1 bar kapanmış. Aynı tf için spot/futures AYNI zaman damgaları."""
    step = TF_MS[tf]
    last_open = (NOW // step) * step
    ts = last_open - np.arange(n)[::-1] * step
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"timestamp": ts, "open": close * 0.999, "high": close * 1.006, "low": close * 0.994, "close": close, "volume": 5.0})


def make_env(root: Path, *, n4h: int = 1000, with_position: bool = True) -> tuple[Path, Path]:
    if root.exists():
        shutil.rmtree(root)
    st, data = root / "state", root / "data"
    st.mkdir(parents=True); data.mkdir()
    for tf, n in (("4h", n4h), ("1h", 600), ("15m", 600), ("1d", 420), ("1w", 300)):
        series(n, tf, 1, 100.0).to_csv(data / f"tv-binance_BTC-USDT_{tf}.csv", index=False)
        series(n, tf, 2, 200.0).to_csv(data / f"binanceusdm_BTC-USDT_{tf}.csv", index=False)
    heads = {"generated_at": "2026-09-15T00:00:00+00:00", "run_id": "r1", "heads": [
        {"symbol": "BTC/USDT", "market_type": "futures", "verdict": "FUTURES_LONG", "direction": "LONG", "no_trade_reason": "", "p_win": 0.5,
         "spot_plan": None, "futures_plan": {"valid": True, "entry": 204.0, "stop": 190.0, "targets": [230.0], "direction": "LONG", "entry_type": "market"},
         "specialist_reports": [], "consensus": {}, "dissent": [], "vetoes": [], "factor_scores": [], "data_freshness": {}}], "chief": {}}
    (st / "coin_heads.json").write_text(json.dumps(heads), encoding="utf-8")
    pos = {"BTC/USDT": {"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "qty": "0.1", "entry_avg": "205", "stop": "190",
                        "targets": ["230"], "opened_at": "2026-09-14T00:00:00+00:00", "leverage": 2, "isolated_margin": "10"}} if with_position else {}
    led = {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100", "equity": "100", "total_fees": "0", "positions": pos, "history": []}
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0, "positions": {}, "history": []}), encoding="utf-8")
    for fn, d in (("killswitch.json", {"state": "ARMED", "since": "", "reasons": [], "audit": []}), ("mode.json", {"mode": "PAPER", "history": []}),
                  ("risk.json", {"profile": {"name": "PAPER_RESEARCH"}, "exposure": {"equity": 100},
                                 "last_decisions": [{"symbol": "BTC/USDT", "verdict": "FUTURES_LONG", "block_code": "NEGATIVE_NET_EDGE", "at": "2026-09-15T00:00:00+00:00"}]})):
        (st / fn).write_text(json.dumps(d), encoding="utf-8")
    # T2 / M2 defterleri (yalnız futures)
    (st / "strategy_paper_index.json").write_text(json.dumps({"books": [
        {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"},
        {"key": "strategy_paper_m2", "name": "m2_tsmom28", "summary_file": "strategy_paper_m2.json"}]}), encoding="utf-8")
    for key, name in (("strategy_paper", "t2_trend_regime"), ("strategy_paper_m2", "m2_tsmom28")):
        (st / key).mkdir()
        (st / key / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "positions": {}, "history": []}), encoding="utf-8")
        (st / f"{key}.json").write_text(json.dumps({"name": name, "key": key, "atr_mult": 3.0, "positions": {}, "counters": {}}), encoding="utf-8")
    return st, data


def bars_of(data: Path, market: str, tf: str = "4h") -> list[dict]:
    df = pd.read_csv(data / (("binanceusdm" if market == "futures" else "tv-binance") + f"_BTC-USDT_{tf}.csv"))
    return [{"timestamp": int(r.timestamp), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)} for r in df.itertuples()]


def snap_main(bars: list[dict], *, as_of: int, market_type: str, position: dict | None, code="repro-sha", cfg_hash="cfgX") -> dict:
    return build_snapshot(symbol="BTC/USDT", market_type=market_type, timeframe="4h", tf_ms=H4, book={"book_id": "main", "name": "main"},
                          bars=closed_bars_at(bars, as_of_ms=as_of, tf="4h"), as_of_ms=as_of, daily_rows=[], btc_daily_rows=[], gates=GATES,
                          decision=None, plan=None, position=position, history=[], entry_features=None, mark_price=None, cfg={"swing_lookback": 3},
                          code=code, cfg_hash=cfg_hash)


def tag(cond: bool, label: str) -> None:
    print(("  BULGU_VAR  " if cond else "  BULGU_YOK  ") + label)


def finding1(root: Path) -> None:
    print("\n[1] desteklenen zaman dilimleri analizde çöküyor mu?")
    st, data = make_env(root / "f1")
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    for tf in ("15m", "1h", "4h", "1d", "1w"):
        r = c.get(f"/api/chart/BTC?tf={tf}&market=futures&book=main&n=100")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:120]
        ok = r.status_code == 200 and isinstance(body, dict) and body.get("analysis") is not None
        print(f"   tf={tf:>3}: HTTP {r.status_code}  analysis={'var' if ok else 'yok'}  {'' if ok else str(body)[:100]}")
    for tf in ("15m", "1w"):
        try:
            closed_bars_at([{"timestamp": NOW - 10 * TF_MS[tf]}], as_of_ms=NOW, tf=tf)
            print(f"   closed_bars_at({tf}): OK")
        except KeyError as exc:
            print(f"   closed_bars_at({tf}): KeyError {exc}")
    r = c.get("/api/chart/BTC?tf=1h&market=futures&book=main&n=100")
    tag(r.status_code != 200, "1h futures grafiği (mum CSV mevcut) 200 dönmüyor")


def finding2(root: Path) -> None:
    print("\n[2] kayıtlı analizde spot/futures ayrımı kayboluyor mu?")
    st, data = make_env(root / "f2")
    fb = bars_of(data, "futures")
    pos = {"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "qty": 0.1, "entry_avg": 205.0, "stop": 190.0, "targets": [230.0], "opened_at": "2026-09-14T00:00:00+00:00"}
    fut_snap = snap_main(fb, as_of=NOW, market_type="USDM_PERP", position=pos)
    ChartAnalysisStore(st).save(fut_snap)
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    j = c.get("/api/chart/BTC?tf=4h&market=spot&book=main&n=100").json()
    ident = (j.get("analysis") or {}).get("identity") or {}
    kinds = {e["kind"] for e in (j.get("analysis") or {}).get("elements", [])}
    print(f"   requested_market=spot  analysis_origin={j.get('analysis_origin')}  analysis.identity.market_type={ident.get('market_type')}")
    print(f"   futures giriş öğesi (kind=entry) spot analizinde: {'var' if 'entry' in kinds else 'yok'};  payload.position={j.get('position')}")
    hist = c.get("/api/chart/BTC/history?tf=4h&market=spot&book=main").json()["rows"]
    print(f"   spot history listesinde futures analysis_id: {'var' if any(r['analysis_id'] == fut_snap['analysis_id'] for r in hist) else 'yok'}")
    r = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n=100&analysis_id={fut_snap['analysis_id']}")
    print(f"   futures analysis_id spot isteğinde: HTTP {r.status_code}")
    tag(ident.get("market_type") == "USDM_PERP" or "entry" in kinds or r.status_code == 200, "spot isteği futures kaydını/pozisyonunu taşıyor")


def finding3(root: Path) -> None:
    print("\n[3A] aynı mum içinde pozisyon kapanınca canlı grafik eski kalıyor mu? (panel önbelleği)")
    st, data = make_env(root / "f3a")
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    j1 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    led = json.loads((st / "futures_ledger.json").read_text(encoding="utf-8"))
    p = led["positions"].pop("BTC/USDT")
    led["history"].append({"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "entry": 205.0, "exit_price": 210.0, "opened_at": p["opened_at"],
                           "closed_at": "2026-09-15T01:00:00+00:00", "exit_reason": "TP1", "net_pnl": 0.5, "r_multiple": 0.33})
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    j2 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    a2 = j2.get("analysis") or {}
    print(f"   1. istek kaynak={j1.get('analysis_origin')}  2. istek kaynak={j2.get('analysis_origin')}")
    print(f"   2. payload.position={j2.get('position')}  2. analysis.position.id={(a2.get('position') or {}).get('id')}")
    print(f"   2. analizde 'entry' (açık giriş) öğesi: {'var' if any(e['kind'] == 'entry' for e in a2.get('elements', [])) else 'yok'};"
          f" 'trade_exit' öğesi: {'var' if any(e['kind'] == 'trade_exit' for e in a2.get('elements', [])) else 'yok'}")
    tag((a2.get("position") or {}).get("id") == "F00001", "kapanmış pozisyon panel önbelleğinden açık gösteriliyor")
    print("\n[3A'] aynı senaryo motor kaydı (store latest) ile")
    st, data = make_env(root / "f3s")
    fb = bars_of(data, "futures")
    pos = {"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "qty": 0.1, "entry_avg": 205.0, "stop": 190.0, "targets": [230.0], "opened_at": "2026-09-14T00:00:00+00:00"}
    ChartAnalysisStore(st).save(snap_main(fb, as_of=NOW - 60_000, market_type="USDM_PERP", position=pos))
    led = json.loads((st / "futures_ledger.json").read_text(encoding="utf-8")); led["positions"] = {}
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    j = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    a = j.get("analysis") or {}
    print(f"   kaynak={j.get('analysis_origin')} historical={j.get('historical')} payload.position={j.get('position')} analysis.position.id={(a.get('position') or {}).get('id')}")
    if j.get("engine_record") is not None:
        print(f"   engine_record: matches_now={j['engine_record'].get('matches_now')} diff={j['engine_record'].get('diff')}")
    tag((a.get("position") or {}).get("id") == "F00001" and not j.get("position"), "motorun eski kaydı 'şimdi' görünümünde açık pozisyon gösteriyor")
    print("\n[3B] miktar/hedef değişimi aynı analysis_id'de kayboluyor mu?")
    st, data = make_env(root / "f3b")
    fb = bars_of(data, "futures")
    p1 = {"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "qty": 2.0, "entry_avg": 100.0, "stop": 90.0, "targets": [120.0], "opened_at": "2026-09-14T00:00:00+00:00"}
    p2 = dict(p1, qty=1.0, targets=[130.0])
    s1 = snap_main(fb, as_of=NOW - 120_000, market_type="USDM_PERP", position=p1)
    s2 = snap_main(fb, as_of=NOW - 60_000, market_type="USDM_PERP", position=p2)
    store = ChartAnalysisStore(st)
    r1, r2 = store.save(s1), store.save(s2)
    try:
        kept = store.latest("main", "USDM_PERP", "BTC/USDT", "4h")      # onarılmış imza (piyasa-kesin)
    except TypeError:
        kept = store.latest("main", "BTC/USDT", "4h")                   # 2e31926 imzası
    files = [p for p in (st / "chart_analysis").rglob("*.json") if p.name != "index.json"]
    print(f"   kayıt dosyası sayısı={len(files)}")
    print(f"   elements farklı={s1['elements'] != s2['elements']}  analysis_id aynı={s1['analysis_id'] == s2['analysis_id']}  ikinci save written={r2['written']}  saklanan qty={kept['position']['qty']} targets={kept['position']['targets']}")
    tag(s1["analysis_id"] == s2["analysis_id"], "miktar/hedef değişimi tekilleştirme yüzünden kayboluyor")


def finding5(root: Path) -> None:
    print("\n[5] pivot teyit zamanı bir mum erken mi?")
    T0 = 1_700_000_000_000
    highs = [100, 101, 102, 110, 103, 102, 101, 100, 99, 98, 97, 96]
    bars = [{"timestamp": T0 + i * H4, "open": h - 1, "high": h, "low": h - 2, "close": h - 0.5} for i, h in enumerate(highs)]
    try:
        piv = pivots(bars, lookback=3, tf_ms=H4)       # onarılmış imza
    except TypeError:
        piv = pivots(bars, lookback=3)                 # 2e31926 imzası
    p = [x for x in piv["confirmed"] if x["index"] == 3][0]
    print(f"   pivot index=3 teyit index={p['confirmed_at_index']}  confirmed_at_ts={p['confirmed_at_ts']}  bar6 açılış={bars[6]['timestamp']}  bar6 kapanış={bars[6]['timestamp'] + H4}")
    s = build_snapshot(symbol="BTC/USDT", market_type="USDM_PERP", timeframe="4h", tf_ms=H4, book={"book_id": "main", "name": "main"}, bars=bars,
                       as_of_ms=bars[-1]["timestamp"] + H4, daily_rows=[], btc_daily_rows=[], gates=GATES, decision=None, plan=None, position=None,
                       history=[], entry_features=None, mark_price=None, cfg={"swing_lookback": 3}, code="x", cfg_hash="y")
    els = [e for e in s["elements"] if e["kind"] == "pivot_high" and e["t0"] == bars[3]["timestamp"]]
    print(f"   öğe confirmed_at={els[0]['confirmed_at'] if els else None} anchors[0].confirmed_at={els[0]['anchors'][0]['confirmed_at'] if els else None}")
    tag(p["confirmed_at_ts"] == bars[6]["timestamp"], "confirmed_at = teyit barının AÇILIŞI (kapanıştan 4 saat erken)")


def finding6(root: Path) -> None:
    print("\n[6] arşivde olan eski mumlar için 'veri yok' mu?")
    st, data = make_env(root / "f6")
    sb = bars_of(data, "spot")
    cut_ts = sb[599]["timestamp"]
    old = snap_main(sb, as_of=cut_ts + H4, market_type="SPOT", position=None)
    assert old["identity"]["last_closed_bar"]["timestamp"] == cut_ts
    ChartAnalysisStore(st).save(old)
    c = TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)
    for n in (50, 300):
        r = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n={n}&analysis_id={old['analysis_id']}")
        j = r.json()
        print(f"   n={n}: HTTP {r.status_code} bars={len(j.get('t') or [])} error={j.get('error')}")
        if r.status_code == 200 and j.get("t"):
            print(f"        max(t) <= analiz son bar: {max(j['t']) <= cut_ts}")
    r = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n=50&analysis_id={old['analysis_id']}")
    tag(r.status_code == 404, "dosyada bulunan tarih için 404 'arşivde yok'")


def identity_bug() -> None:
    print("\n[K] JS kimlik alanı: build_snapshot kökte analysis_id, JS identity.analysis_id okuyor mu?")
    from tradingbot.dashboard.chart_js import CHART_JS
    s = build_snapshot(symbol="BTC/USDT", market_type="USDM_PERP", timeframe="4h", tf_ms=H4, book={"book_id": "main", "name": "main"}, bars=[],
                       as_of_ms=NOW, daily_rows=[], btc_daily_rows=[], gates=GATES, decision=None, plan=None, position=None, history=[],
                       entry_features=None, mark_price=None, cfg={}, code="x", cfg_hash="y")
    print(f"   snapshot.analysis_id={s['analysis_id']}  snapshot.identity.analysis_id={s['identity'].get('analysis_id')}")
    print(f"   CHART_JS 'a.analysis_id' (identity üzerinden) okuma sayısı: {CHART_JS.count('a.analysis_id')}")
    tag("a.analysis_id" in CHART_JS and s["identity"].get("analysis_id") is None, "JS kimliği identity altında arıyor, snapshot kökte taşıyor")


if __name__ == "__main__":
    for fn in (finding1, finding2, finding3, finding5, finding6):
        try:
            fn(WORK)
        except Exception:  # noqa: BLE001
            print("  !! yeniden üretim betiği hata verdi:")
            traceback.print_exc()
    identity_bug()
