# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — bağımsız incelemenin altı bulgusu + JS kimlik hatası için DAVRANIŞ testleri (2026-09-15).

Her test 2e31926'daki hatalı davranışı docstring'inde tanımlar ve onarılmış sonucu GERÇEK FastAPI uygulaması (TestClient),
gerçek `ChartAnalysisStore` ve (node varsa) gerçek `CHART_JS` metni üzerinde doğrular. Sentetik state: iki piyasa AYNI
zaman damgalarıyla, F00001 açık LONG, T2/M2 kâğıt defterleri, motorun yazdığı `chart_analysis/config.json`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from test_chart_patterns_v1 import DOUBLE_BOTTOM_PATH, _bars as _pattern_bars  # noqa: E402
from tradingbot import candle_confirmation  # noqa: E402
from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.chart_analysis import (MARKET_TYPES, build_snapshot, closed_bars_at, decision_fingerprint_payload,  # noqa: E402
                                       market_type_of, pivots)
from tradingbot.chart_analysis_store import INDEX_SCHEMA, ChartAnalysisStore, series_key  # noqa: E402
from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402
from tradingbot.dashboard.candles import CandleSource  # noqa: E402
from tradingbot.dashboard.chart_js import CHART_JS  # noqa: E402
from tradingbot.timeframes import SUPPORTED_TIMEFRAMES, TF_MS, bar_close_ms, is_closed, tf_ms  # noqa: E402

H4 = TF_MS["4h"]
NOW = int(time.time() * 1000)
GATES = {"candle_mode": "ENFORCE", "candle_variant": "c3_4h_veto", "chart_mode": "SHADOW", "chart_variant": "p2_4h_veto",
         "regime_mode": "ENFORCE", "regime_variant": "r1_long_only_uptrend", "chart_fresh_within": 3}
CFG = {"swing_lookback": 3, "cluster_tolerance_atr": 0.10, "trendline_touch_tolerance_pct": 0.3}
CODE, CFG_HASH = "c0ffee00", "cfg-h1"
POS = {"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "qty": "0.1", "entry_avg": "205", "stop": "190", "targets": ["230"],
       "opened_at": "2026-09-14T00:00:00+00:00", "leverage": 2, "isolated_margin": "10"}
PLAN = {"valid": True, "entry": 204.0, "stop": 190.0, "targets": [230.0], "direction": "LONG", "entry_type": "market"}
DECISION = {"symbol": "BTC/USDT", "verdict": "FUTURES_LONG", "block_code": "NEGATIVE_NET_EDGE", "at": "2026-09-15T00:00:00+00:00"}


def _series(n: int, tf: str, seed: int, start: float) -> pd.DataFrame:
    """Son bar 'şimdi' açık (kapanmamış); öncekiler kapanmış. Aynı dilimde spot/futures AYNI zaman damgaları."""
    step = TF_MS[tf]
    last_open = (NOW // step) * step
    ts = last_open - np.arange(n)[::-1] * step
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"timestamp": ts, "open": close * 0.999, "high": close * 1.006, "low": close * 0.994, "close": close, "volume": 5.0})


def _env(tmp_path: Path, *, n4h: int = 1000, with_position: bool = True, with_config: bool = True) -> tuple[Path, Path]:
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(); data.mkdir()
    for tf, n in (("4h", n4h), ("1h", 600), ("15m", 600), ("1d", 420), ("1w", 300)):
        _series(n, tf, 1, 100.0).to_csv(data / f"tv-binance_BTC-USDT_{tf}.csv", index=False)
        _series(n, tf, 2, 200.0).to_csv(data / f"binanceusdm_BTC-USDT_{tf}.csv", index=False)
    heads = {"generated_at": "2026-09-15T00:00:00+00:00", "run_id": "r1", "heads": [
        {"symbol": "BTC/USDT", "market_type": "futures", "verdict": "FUTURES_LONG", "direction": "LONG", "no_trade_reason": "", "p_win": 0.5,
         "spot_plan": None, "futures_plan": dict(PLAN), "specialist_reports": [], "consensus": {}, "dissent": [], "vetoes": [], "factor_scores": [], "data_freshness": {}}], "chief": {}}
    (st / "coin_heads.json").write_text(json.dumps(heads), encoding="utf-8")
    led = {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100", "equity": "100", "total_fees": "0",
           "positions": {"BTC/USDT": dict(POS)} if with_position else {}, "history": []}
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0, "positions": {}, "history": []}), encoding="utf-8")
    for fn, d in (("killswitch.json", {"state": "ARMED", "since": "", "reasons": [], "audit": []}), ("mode.json", {"mode": "PAPER", "history": []}),
                  ("risk.json", {"profile": {"name": "PAPER_RESEARCH"}, "exposure": {"equity": 100}, "last_decisions": [dict(DECISION)]})):
        (st / fn).write_text(json.dumps(d), encoding="utf-8")
    (st / "strategy_paper_index.json").write_text(json.dumps({"books": [
        {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"},
        {"key": "strategy_paper_m2", "name": "m2_tsmom28", "summary_file": "strategy_paper_m2.json"}]}), encoding="utf-8")
    for key, name in (("strategy_paper", "t2_trend_regime"), ("strategy_paper_m2", "m2_tsmom28")):
        (st / key).mkdir()
        (st / key / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "positions": {}, "history": []}), encoding="utf-8")
        (st / f"{key}.json").write_text(json.dumps({"name": name, "key": key, "atr_mult": 3.0, "positions": {}, "counters": {}}), encoding="utf-8")
    if with_config:
        (st / "chart_analysis").mkdir()
        (st / "chart_analysis" / "config.json").write_text(json.dumps({"generated_at": "2026-09-15T00:00:00Z", "gates": GATES, "cfg": CFG, "timeframe": "4h",
                                                                        "code_sha": CODE, "config_hash": CFG_HASH, "keep_per_series": 300}), encoding="utf-8")
    return st, data


def _bars(data: Path, market: str, tf: str = "4h") -> list[dict]:
    df = CandleSource(data).load("BTC", tf, market)
    return [{"timestamp": int(r.timestamp), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)} for r in df.itertuples()]


def _snap(bars: list[dict], *, as_of: int, market_type: str, book=("main", "main"), position=None, history=None, plan=None, decision=None,
          mark_price=None, code=CODE, cfg_hash=CFG_HASH, tf="4h") -> dict:
    return build_snapshot(symbol="BTC/USDT", market_type=market_type, timeframe=tf, tf_ms=TF_MS[tf], book={"book_id": book[0], "name": book[1], "atr_mult": 3.0},
                          bars=closed_bars_at(bars, as_of_ms=as_of, tf=tf), as_of_ms=as_of, daily_rows=[], btc_daily_rows=[], gates=GATES,
                          decision=decision, plan=plan, position=position, history=history or [], entry_features=None, mark_price=mark_price, cfg=CFG,
                          code=code, cfg_hash=cfg_hash)


def _client(st: Path, data: Path) -> TestClient:
    return TestClient(create_app(st, data, None, DashboardConfig()), raise_server_exceptions=False)


def _ledger(st: Path) -> dict:
    return json.loads((st / "futures_ledger.json").read_text(encoding="utf-8"))


def _write_ledger(st: Path, led: dict) -> None:
    p = st / "futures_ledger.json"
    p.write_text(json.dumps(led), encoding="utf-8")
    os.utime(p, None)


def _kinds(j: dict) -> dict:
    return {e["kind"]: e for e in ((j.get("analysis") or {}).get("elements") or [])}


def _main_state_snapshot(st: Path, data: Path, *, as_of: int) -> dict:
    """Motorun bu state için yazacağı ana defter/futures kaydı: panelle AYNI girdiler (defter, plan, karar, kod/config)."""
    led = _ledger(st)
    pos = led["positions"].get("BTC/USDT")
    if pos is not None:
        pos = dict(pos); pos.setdefault("entry_avg", pos.get("entry")); pos.setdefault("qty", pos.get("units"))
    hist = [h for h in led.get("history") or [] if h.get("symbol") == "BTC/USDT"]
    df = CandleSource(data).load("BTC", "4h", "futures")
    return _snap(_bars(data, "futures"), as_of=as_of, market_type="USDM_PERP", position=pos, history=hist, plan=dict(PLAN), decision=dict(DECISION),
                 mark_price=float(df["close"].iloc[-1]))


# ====================================================================== BULGU 1: zaman dilimleri
def test_f1_every_supported_timeframe_serves_analysis_and_unknown_tf_is_rejected(tmp_path: Path):
    """2e31926: `api_chart` 15m/1h/4h/1d/1w kabul ediyor, `candle_confirmation._TF_MS` yalnız 4h/1d → 1h isteği KeyError (HTTP 500).
    Onarım: dilimler tek kaynak (`timeframes`); beş dilim de mevcut veriyle 200; bilinmeyen dilim 400 (sessizce 4h olmaz)."""
    st, data = _env(tmp_path)
    c = _client(st, data)
    for tf in SUPPORTED_TIMEFRAMES:
        r = c.get(f"/api/chart/BTC?tf={tf}&market=futures&book=main&n=100")
        assert r.status_code == 200, (tf, r.text[:200])
        j = r.json()
        assert j["analysis"] is not None and j["analysis"]["identity"]["timeframe"] == tf and j["tf_ms"] == TF_MS[tf]
        assert j["analysis"]["identity"]["last_closed_bar"]["timestamp"] + TF_MS[tf] <= NOW + 60_000, "son kapanmış bar gerçekten kapanmış olmalı"
        assert c.get(f"/api/chart/BTC/history?tf={tf}&market=futures&book=main").status_code == 200
    for url in ("/api/chart/BTC?tf=3h&market=futures&book=main", "/api/chart/BTC/history?tf=3h&market=futures&book=main", "/api/candles/BTC?tf=3h&market=futures"):
        r = c.get(url)
        assert r.status_code == 400 and "desteklenmeyen zaman dilimi" in r.json()["detail"], url
    os.remove(data / "binanceusdm_BTC-USDT_15m.csv")                       # eksik dilim: açık ve kontrollü, 4h ile doldurulmaz
    r = c.get("/api/chart/BTC?tf=15m&market=futures&book=main&n=100")
    assert r.status_code == 404 and r.json()["source"]["missing"] is True and r.json()["analysis"] is None
    assert c.get("/coin/BTC?market=futures&book=main&tf=1w").status_code == 200


def test_f1_closed_bar_boundary_is_shared_single_source():
    """Mum, açılış + dilim süresinden 1 ms önce analize GİREMEZ; tam kapanış anında girebilir — her dilimde, tek kaynaktan."""
    assert candle_confirmation._TF_MS is TF_MS and tuple(TF_MS) == SUPPORTED_TIMEFRAMES
    for tf, step in TF_MS.items():
        rows = [{"timestamp": 1_700_000_000_000}]
        assert closed_bars(rows, now_ms=1_700_000_000_000 + step - 1, tf=tf) == []
        assert closed_bars(rows, now_ms=1_700_000_000_000 + step, tf=tf) == rows
        assert closed_bars_at(rows, as_of_ms=1_700_000_000_000 + step - 1, tf=tf) == [] and closed_bars_at(rows, as_of_ms=1_700_000_000_000 + step, tf=tf) == rows
        assert bar_close_ms(1_700_000_000_000, tf) == 1_700_000_000_000 + step and tf_ms(tf) == step
        assert not is_closed(1_700_000_000_000, tf, now_ms=1_700_000_000_000 + step - 1) and is_closed(1_700_000_000_000, tf, now_ms=1_700_000_000_000 + step)
    with pytest.raises(ValueError):
        closed_bars([{"timestamp": 1}], now_ms=10**15, tf="2h")
    with pytest.raises(ValueError):
        market_type_of("perp")


# ====================================================================== BULGU 2: spot/futures ayrımı kayıtta
def test_f2_stored_analysis_is_market_strict_in_store_api_history_and_explicit_id(tmp_path: Path):
    """2e31926: seri anahtarı book|symbol|tf; futures kaydı (F00001 açık LONG) spot isteğine 'son kayıt' olarak dönüyor, spot
    history listesinde görünüyor ve analysis_id spot isteğinde kabul ediliyordu. Onarım: piyasa saklama/listeleme/son kayıt/
    açık kimlik/önbellek boyunca kimliğin parçası."""
    st, data = _env(tmp_path)
    fb, sb = _bars(data, "futures"), _bars(data, "spot")
    assert [b["timestamp"] for b in fb] == [b["timestamp"] for b in sb], "iki piyasa AYNI zaman damgalarıyla test edilir"
    rec = _main_state_snapshot(st, data, as_of=NOW - 1_000)
    assert rec["identity"]["market_type"] == "USDM_PERP" and any(e["kind"] == "entry" for e in rec["elements"])
    assert ChartAnalysisStore(st).save(rec)["written"] is True
    c = _client(st, data)
    js = c.get("/api/chart/BTC?tf=4h&market=spot&book=main&n=100").json()
    assert js["analysis"]["identity"]["market_type"] == "SPOT" and js["position"] == {} and js["plan"] == {}
    assert "entry" not in _kinds(js) and "plan_entry" not in _kinds(js) and js["analysis_origin"] != "store" and js["analysis_stored"] is False
    assert js["engine_record"] is None, "spot serisinde motor kaydı YOK; futures kaydı spot'a taşınmaz"
    assert c.get("/api/chart/BTC/history?tf=4h&market=spot&book=main").json()["rows"] == []
    fut_rows = c.get("/api/chart/BTC/history?tf=4h&market=futures&book=main").json()
    assert [r["analysis_id"] for r in fut_rows["rows"]] == [rec["analysis_id"]] and fut_rows["market_type"] == "USDM_PERP"
    r = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n=100&analysis_id={rec['analysis_id']}")
    assert r.status_code == 404 and "piyasa" in r.json()["detail"] and "USDM_PERP" in r.json()["detail"]
    jf = c.get(f"/api/chart/BTC?tf=4h&market=futures&book=main&n=100&analysis_id={rec['analysis_id']}").json()
    assert jf["historical"] is True and _kinds(jf)["entry"]["price"] == 205.0 and jf["position"]["entry"] == 205.0
    jn = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert jn["analysis_origin"] == "store" and jn["analysis_stored"] is True and jn["analysis"]["analysis_id"] == rec["analysis_id"]
    assert jn["engine_record"]["matches_now"] is True and jn["position"]["entry"] == 205.0
    # T2/M2: futures kapsamı korunur; spot grafiği kâğıt defter pozisyonu/kaydı taşımaz
    t2 = json.loads((st / "strategy_paper" / "futures_ledger.json").read_text(encoding="utf-8"))
    t2["positions"] = {"BTC/USDT": dict(POS, id="F00009", targets=[])}
    (st / "strategy_paper" / "futures_ledger.json").write_text(json.dumps(t2), encoding="utf-8")
    jt = c.get("/api/chart/BTC?tf=4h&market=futures&book=strategy_paper&n=100").json()
    assert jt["position"]["entry"] == 205.0 and "no_target" in _kinds(jt) and jt["analysis"]["identity"]["book_id"] == "strategy_paper"
    jts = c.get("/api/chart/BTC?tf=4h&market=spot&book=strategy_paper&n=100").json()
    assert jts["position"] == {} and "entry" not in _kinds(jts)
    # store: aynı sembol/dilim/defter, iki piyasa iki ayrı seri ve iki ayrı dizin
    idx = ChartAnalysisStore(st).index()
    assert set(idx["series"]) == {series_key("main", "USDM_PERP", "BTC/USDT", "4h")} and idx["schema_version"] == INDEX_SCHEMA
    assert (st / "chart_analysis" / "main" / "USDM_PERP" / "BTC_USDT_4h").exists()


def test_f2_legacy_v1_index_rows_resolve_market_from_file_or_stay_unlisted(tmp_path: Path):
    """Uyumluluk politikası: v1 indeks satırı (piyasasız) dosyasındaki identity.market_type ile serisine taşınır; piyasası
    okunamayan satır hiçbir piyasada listelenmez (başka piyasaya ATANMAZ) ama kimlikle yüklenir; dosyalar değişmez."""
    st = tmp_path / "state"; (st / "chart_analysis" / "main" / "BTC_USDT_4h").mkdir(parents=True)
    root = st / "chart_analysis"
    bars = _bars_ts(60)
    good = _snap(bars, as_of=bars[-1]["timestamp"] + H4, market_type="USDM_PERP")
    bad = json.loads(json.dumps(_snap(bars, as_of=bars[-1]["timestamp"] + H4, market_type="SPOT", cfg_hash="other")))
    bad["identity"].pop("market_type")                                          # piyasası bilinmeyen eski kayıt
    (root / "main" / "BTC_USDT_4h" / ("1_%s.json" % good["analysis_id"])).write_text(json.dumps(good), encoding="utf-8")
    (root / "main" / "BTC_USDT_4h" / ("2_%s.json" % bad["analysis_id"])).write_text(json.dumps(bad), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"schema_version": "chart_analysis_index_v1", "series": {"main|BTC/USDT|4h": [
        {"analysis_id": good["analysis_id"], "as_of_ms": 1, "file": "main/BTC_USDT_4h/1_%s.json" % good["analysis_id"]},
        {"analysis_id": bad["analysis_id"], "as_of_ms": 2, "file": "main/BTC_USDT_4h/2_%s.json" % bad["analysis_id"]}]}}), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in (root / "main" / "BTC_USDT_4h").glob("*.json")}
    store = ChartAnalysisStore(st)
    assert [r["analysis_id"] for r in store.list("main", "USDM_PERP", "BTC/USDT", "4h")] == [good["analysis_id"]]
    assert store.list("main", "SPOT", "BTC/USDT", "4h") == [] and store.list("main", "?", "BTC/USDT", "4h") == []
    assert store.latest("main", "USDM_PERP", "BTC/USDT", "4h")["analysis_id"] == good["analysis_id"]
    assert store.load(bad["analysis_id"])["analysis_id"] == bad["analysis_id"], "kimlikle yükleme çalışır; panel piyasayı ayrıca doğrular"
    assert store.stats()["upgraded_from"] == "chart_analysis_index_v1"
    new = _snap(bars[:-1], as_of=bars[-2]["timestamp"] + H4, market_type="SPOT")
    assert store.save(new)["written"] is True
    idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert idx["schema_version"] == INDEX_SCHEMA and set(idx["series"]) == {series_key("main", "USDM_PERP", "BTC/USDT", "4h"), series_key("main", "?", "BTC/USDT", "4h"), series_key("main", "SPOT", "BTC/USDT", "4h")}
    assert {p.name: p.read_bytes() for p in (root / "main" / "BTC_USDT_4h").glob("*.json")} == before, "eski kayıt dosyaları geriye dönük DEĞİŞMEZ"
    assert (root / "main" / "SPOT" / "BTC_USDT_4h").exists()


def _bars_ts(n: int = None) -> list[dict]:
    bars = _pattern_bars(DOUBLE_BOTTOM_PATH)
    for i, b in enumerate(bars):
        b["timestamp"] = 1_700_000_000_000 + i * H4
    return bars[:n] if n else bars


# ====================================================================== BULGU 3: aynı mum içinde defter değişimi
def test_f3a_now_view_follows_ledger_changes_within_the_same_bar_on_panel_path(tmp_path: Path):
    """2e31926: panel önbellek anahtarında defter/karar yoktu → pozisyon kapansa da 'panel-cache' F00001'i açık gösteriyordu.
    Onarım: önbellek anahtarı canlı dosya sürümlerini içerir; açılış, kısmi kapanış, stop/TP değişimi ve kapanış aynı barda görünür."""
    st, data = _env(tmp_path, with_config=False)
    c = _client(st, data)
    j1 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j1["analysis_origin"] == "panel-ephemeral" and j1["analysis"]["position"]["id"] == "F00001" and _kinds(j1)["entry"]["price"] == 205.0
    assert j1["live"]["position"]["id"] == "F00001" and j1["live"]["source"] == "futures_ledger.json" and j1["live"]["as_of_ms"] > 0
    j1b = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j1b["analysis_origin"] == "panel-cache" and j1b["analysis"]["analysis_id"] == j1["analysis"]["analysis_id"]
    led = _ledger(st)                                                            # KISMİ kapanış + stop + hedef değişimi, aynı bar
    led["positions"]["BTC/USDT"].update({"qty": "0.05", "stop": "195", "targets": ["240"]})
    led["history"].append({"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "entry": 205.0, "exit_price": 212.0, "qty": 0.05, "opened_at": POS["opened_at"],
                           "closed_at": "2026-09-15T01:00:00+00:00", "exit_reason": "PARTIAL_TP", "net_pnl": 0.3, "r_multiple": 0.4})
    _write_ledger(st, led)
    j2 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    k2 = _kinds(j2)
    assert j2["analysis_origin"] == "panel-ephemeral" and j2["analysis"]["analysis_id"] != j1["analysis"]["analysis_id"]
    assert j2["position"]["qty"] == 0.05 and j2["position"]["stop"] == 195.0 and j2["position"]["tp1"] == 240.0
    assert k2["stop"]["price"] == 195.0 and k2["target"]["price"] == 240.0 and "0.05" in k2["entry"]["label_tr"] and k2["trade_exit"]["price"] == 212.0
    led["positions"] = {}                                                       # tam kapanış, aynı bar
    _write_ledger(st, led)
    j3 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j3["position"] == {} and j3["analysis"]["position"] is None and "entry" not in _kinds(j3) and "trade_exit" in _kinds(j3)
    assert j3["analysis"]["analysis_id"] not in (j1["analysis"]["analysis_id"], j2["analysis"]["analysis_id"])
    j3b = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j3b["analysis_origin"] == "panel-cache" and j3b["analysis"]["analysis_id"] == j3["analysis"]["analysis_id"], "değişiklik yokken önbellek"
    assert not (st / "chart_analysis").exists(), "panel hiçbir kayıt yazmaz"


def test_f3a_engine_record_is_shown_only_when_it_matches_now_and_live_mark_is_fresh(tmp_path: Path):
    """2e31926: motorun son kaydı son kapanmış barla aynıysa 'şimdi' sayılıyordu → pozisyon kapansa da eski kayıt açık gösteriyordu.
    Onarım sözleşmesi: 'şimdi' = güncel bar + güncel defter/karar ile hesaplanan kimlik; motor kaydı yalnız kimlik AYNIYSA
    gösterilir (işaret fiyatı canlı), değilse panel hesabı + motor kaydının farkı bildirilir; kod/config değişince eski kayıt
    güncel sayılmaz; kayıt dosyası hiçbir adımda değişmez."""
    st, data = _env(tmp_path)
    rec = _main_state_snapshot(st, data, as_of=NOW - 5_000)
    store = ChartAnalysisStore(st)
    store.save(rec)
    rec_file = next(p for p in (st / "chart_analysis").rglob("*_%s.json" % rec["analysis_id"]))
    rec_bytes = rec_file.read_bytes()
    c = _client(st, data)
    j = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j["analysis_origin"] == "store" and j["analysis_stored"] is True and j["historical"] is False
    assert j["analysis"]["analysis_id"] == rec["analysis_id"] and j["engine_record"] == {**j["engine_record"], "matches_now": True, "diff": []}
    mark = _kinds(j)["mark"]
    assert abs(mark["price"] - j["c"][-1]) < 1e-6 and mark["source"]["live"] is True and "canlı" in mark["label_tr"]
    assert j["live"]["position"]["id"] == "F00001" and abs(j["live"]["mark_price"] - j["c"][-1]) < 1e-6
    led = _ledger(st); led["positions"] = {}
    led["history"].append({"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "entry": 205.0, "exit_price": 210.0, "opened_at": POS["opened_at"],
                           "closed_at": "2026-09-15T01:00:00+00:00", "exit_reason": "TP1", "net_pnl": 0.5, "r_multiple": 0.33})
    _write_ledger(st, led)
    j2 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j2["analysis_origin"] == "panel-ephemeral" and j2["analysis_stored"] is False
    assert j2["position"] == {} and j2["analysis"]["position"] is None and "entry" not in _kinds(j2) and "trade_exit" in _kinds(j2)
    er = j2["engine_record"]
    assert er["analysis_id"] == rec["analysis_id"] and er["matches_now"] is False and any("defter/karar" in d for d in er["diff"])
    assert c.get("/api/chart/BTC/history?tf=4h&market=futures&book=main").json()["rows"][-1]["analysis_id"] == rec["analysis_id"], "eski kayıt geçmişte durur"
    h = c.get(f"/api/chart/BTC?tf=4h&market=futures&book=main&n=100&analysis_id={rec['analysis_id']}").json()
    assert h["historical"] is True and h["position"]["entry"] == 205.0 and _kinds(h)["entry"]["price"] == 205.0, "tarihsel görünüm o anki defteri gösterir"
    _write_ledger(st, _ledger(st) | {"positions": {"BTC/USDT": dict(POS)}, "history": []})   # defter geri geldi
    cfg = json.loads((st / "chart_analysis" / "config.json").read_text(encoding="utf-8")); cfg["code_sha"] = "deadbeef"
    (st / "chart_analysis" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    j3 = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()
    assert j3["analysis_stored"] is False and any("kod farklı" in d for d in j3["engine_record"]["diff"]), "kod değişince eski kayıt güncel sayılmaz"
    assert rec_file.read_bytes() == rec_bytes and store.stats()["snapshots"] == 1


def test_f3b_fingerprint_captures_qty_targets_stop_and_close_but_not_mark_price(tmp_path: Path):
    """2e31926: parmak izinde qty/targets/history yoktu → aynı bar içinde 2 adet→1 adet, TP 120→130 aynı analysis_id; store ikinci
    kaydı yazmıyor, saklanan miktar 2 kalıyordu. Onarım: kayda değer değişim yeni kimlik; işaret fiyatı kimliğe girmez;
    önceki dosya byte-aynı kalır; özdeş tekrar dosya çoğaltmaz."""
    bars = _bars_ts()
    as_of = bars[-1]["timestamp"] + H4
    base = dict(id="F00001", symbol="BTC/USDT", side="LONG", entry_avg=100.0, stop=90.0, opened_at="2026-09-14T00:00:00+00:00")
    s_a = _snap(bars, as_of=as_of, market_type="USDM_PERP", position=dict(base, qty=2.0, targets=[120.0]), mark_price=101.0)
    s_b = _snap(bars, as_of=as_of + 60_000, market_type="USDM_PERP", position=dict(base, qty=1.0, targets=[130.0]), mark_price=101.0)
    s_stop = _snap(bars, as_of=as_of, market_type="USDM_PERP", position=dict(base, qty=2.0, targets=[120.0], stop=95.0), mark_price=101.0)
    s_close = _snap(bars, as_of=as_of + 240_000, market_type="USDM_PERP", position=None,
                    history=[{"id": "F00001", "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "exit_price": 120.0, "opened_at": as_of - 3 * H4, "closed_at": as_of, "exit_reason": "TP1"}], mark_price=101.0)
    s_mark = _snap(bars, as_of=as_of + 120_000, market_type="USDM_PERP", position=dict(base, qty=2.0, targets=[120.0]), mark_price=150.0)
    s_same = _snap(bars, as_of=as_of + 180_000, market_type="USDM_PERP", position=dict(base, qty=2.0, targets=[120.0]), mark_price=101.0)
    ids = {k: v["analysis_id"] for k, v in (("a", s_a), ("b", s_b), ("stop", s_stop), ("close", s_close))}
    assert len(set(ids.values())) == 4, ids
    assert s_mark["analysis_id"] == s_a["analysis_id"] == s_same["analysis_id"], "işaret fiyatı / zaman geçişi kimliği DEĞİŞTİRMEZ"
    fp = decision_fingerprint_payload(rs=None, decision=None, plan=None, position=dict(base, qty=2.0, targets=[120.0]), history=[])
    assert fp["position"]["qty"] == 2.0 and fp["position"]["targets"] == [120.0] and fp["history"] == {"n": 0, "last": None}
    store = ChartAnalysisStore(tmp_path)
    assert store.save(s_a)["written"] is True
    f_a = next(p for p in (tmp_path / "chart_analysis").rglob("*_%s.json" % s_a["analysis_id"]))
    bytes_a = f_a.read_bytes()
    assert store.save(s_b)["written"] is True and store.latest("main", "USDM_PERP", "BTC/USDT", "4h")["position"]["qty"] == 1.0
    assert store.save(s_same)["written"] is False and store.save(s_mark)["written"] is False, "özdeş gerçek durum tekrarında dosya çoğalmaz"
    assert store.save(s_close)["written"] is True and store.latest("main", "USDM_PERP", "BTC/USDT", "4h")["position"] is None
    assert f_a.read_bytes() == bytes_a and store.stats()["snapshots"] == 3


# ====================================================================== BULGU 5: teyit zamanı
def test_f5_pivot_and_pattern_confirmation_is_bar_close_not_open():
    """2e31926: confirmed_at = teyit barının AÇILIŞI (4 saat erken). Onarım: teyit/kırılış/tanınma barı KAPANIŞI; teyit barı
    kapanmadan 1 ms önce pivot teyitli olamaz, kapanışta olur; çizim x koordinatı (timestamp) kaydırılmaz."""
    T0 = 1_700_000_000_000
    highs = [100, 101, 102, 110, 103, 102, 101, 100, 99, 98, 97, 96]
    bars = [{"timestamp": T0 + i * H4, "open": h - 1, "high": h, "low": h - 2, "close": h - 0.5} for i, h in enumerate(highs)]
    p = [x for x in pivots(bars, lookback=3, tf_ms=H4)["confirmed"] if x["index"] == 3][0]
    assert p["confirmed_at_index"] == 6 and p["confirmed_bar_open_ts"] == bars[6]["timestamp"] and p["confirmed_at_ts"] == bars[6]["timestamp"] + H4
    close6 = bars[6]["timestamp"] + H4
    before = _snap(bars, as_of=close6 - 1, market_type="USDM_PERP")
    assert not [x for x in before["pivots"]["confirmed"] if x["timestamp"] == bars[3]["timestamp"]], "teyit barı kapanmadan pivot teyitli DEĞİL"
    assert before["pivots"]["pending"] and before["pivots"]["pending"]["timestamp"] == bars[3]["timestamp"]
    at = _snap(bars, as_of=close6, market_type="USDM_PERP")
    piv3 = [x for x in at["pivots"]["confirmed"] if x["timestamp"] == bars[3]["timestamp"]][0]
    assert piv3["confirmed_at"] == close6 == at["identity"]["as_of_ms"] and piv3["confirmed_bar_open"] == bars[6]["timestamp"]
    el = [e for e in at["elements"] if e["kind"] == "pivot_high" and e["t0"] == bars[3]["timestamp"]][0]
    assert el["confirmed_at"] == close6 and el["anchors"][0]["confirmed_at"] == close6 and el["t0"] == bars[3]["timestamp"]
    for e in at["elements"]:
        if e.get("confirmed_at") is not None:
            assert e["confirmed_at"] <= at["identity"]["as_of_ms"], e["id"]
        for a in e.get("anchors") or []:
            assert a["confirmed_at"] is None or a["confirmed_at"] <= at["identity"]["as_of_ms"]
    assert at["time_contract"]["confirmed_at"].startswith("teyit barının KAPANIŞI")
    # formasyon: dedektör alanları bar açılışı (DEĞİŞMEDİ); gösterim alanları kapanış
    db = _bars_ts()
    s = _snap(db, as_of=db[-1]["timestamp"] + H4, market_type="USDM_PERP")
    pat = [e for e in s["elements"] if e["layer"] == "patterns"][0]
    det = [p for p in s["patterns_detected"] if p["pattern"] == "DOUBLE_BOTTOM"][0]
    assert det["recognition_ts"] == db[det["recognition_index"]]["timestamp"] and det["break_ts"] == db[det["break_index"]]["timestamp"]
    assert pat["recognition_at"] == det["recognition_ts"] and pat["confirmed_at"] == pat["recognition_known_at"] == det["recognition_ts"] + H4
    assert pat["break_at"] == det["break_ts"] and pat["break_known_at"] == det["break_ts"] + H4 and pat["confirmed_at"] <= s["identity"]["as_of_ms"]
    assert all(a["confirmed_at"] == a["confirmed_bar_open"] + H4 for a in pat["anchors"])
    early = _snap(db, as_of=det["recognition_ts"] + H4 - 1, market_type="USDM_PERP")
    assert not [e for e in early["elements"] if e["layer"] == "patterns" and e["pattern"] == "DOUBLE_BOTTOM"], "tanınma barı kapanmadan formasyon yok"


def test_f5_stored_record_api_and_history_views_carry_the_same_confirmation_times(tmp_path: Path):
    st, data = _env(tmp_path)
    rec = _main_state_snapshot(st, data, as_of=NOW - 1_000)
    ChartAnalysisStore(st).save(rec)
    c = _client(st, data)
    live = c.get("/api/chart/BTC?tf=4h&market=futures&book=main&n=100").json()["analysis"]
    hist = c.get(f"/api/chart/BTC?tf=4h&market=futures&book=main&n=100&analysis_id={rec['analysis_id']}").json()["analysis"]
    dl = json.loads(c.get(f"/api/chart/BTC/snapshot/{rec['analysis_id']}").content)
    assert live["pivots"] == hist["pivots"] == dl["pivots"] == rec["pivots"] and rec["pivots"]["confirmed"]
    step = TF_MS["4h"]
    for p in rec["pivots"]["confirmed"]:
        assert p["confirmed_at"] == p["confirmed_bar_open"] + step and p["confirmed_at"] <= rec["identity"]["as_of_ms"]


# ====================================================================== BULGU 6: geçmiş mumlar
def test_f6_historical_candles_are_selected_by_analysis_moment_not_by_newest_tail(tmp_path: Path):
    """2e31926: önce en yeni n+250 bar alınıyor, sonra analiz tarihine kesiliyordu → arşivde bulunan eski tarih için 404.
    Onarım: `end_ts` ile analiz anına göre seçim; n=50 ve n=300 açılır; analiz anından sonraki mum görünmez; gerçekten eksik
    tarih arşiv aralığıyla birlikte kontrollü 404."""
    st, data = _env(tmp_path, n4h=1000)
    sb = _bars(data, "spot")
    cut = sb[599]["timestamp"]
    old = _snap(sb, as_of=cut + H4, market_type="SPOT")
    assert old["identity"]["last_closed_bar"]["timestamp"] == cut
    ChartAnalysisStore(st).save(old)
    c = _client(st, data)
    for n in (50, 300):
        j = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n={n}&analysis_id={old['analysis_id']}")
        assert j.status_code == 200, j.text[:200]
        d = j.json()
        assert d["historical"] is True and len(d["t"]) == n and max(d["t"]) == cut and d["t"][0] == sb[600 - n]["timestamp"]
        assert d["overlays"]["ema200"][-1] is not None, "gösterge ısınması analiz tarihinden geriye alınır"
    # gerçekten eksik: arşivin ilk barından önceki bir analiz anı
    first = sb[0]["timestamp"]
    fake = json.loads(json.dumps(old)); fake["analysis_id"] = "deadbeefdeadbeef"; fake["identity"]["as_of_ms"] = first - 5 * H4
    fake["identity"]["last_closed_bar"]["timestamp"] = first - 6 * H4
    ChartAnalysisStore(st).save(fake)
    r = c.get(f"/api/chart/BTC?tf=4h&market=spot&book=main&n=50&analysis_id={fake['analysis_id']}")
    assert r.status_code == 404 and "arşivde yok" in r.json()["error"] and "arşiv" in r.json()["error"] and "1000 bar" in r.json()["error"]
    assert r.json()["analysis"]["analysis_id"] == fake["analysis_id"]


# ====================================================================== motor kaydı: piyasa-kesin, panelle aynı kimlik
def test_engine_records_are_market_strict_and_panel_now_view_matches_them(tmp_path: Path, monkeypatch):
    """Motor kaydı defterin piyasasını taşır: T2/M2 her zaman USDM_PERP (futures defteri); ana bot çerçeve piyasasında ve
    o piyasanın pozisyonu/planı/geçmişiyle (ağsız test motorunda çerçeve SPOT → ana kayıt futures pozisyonu TAŞIMAZ).
    Panelin 'şimdi' görünümü aynı state ile motor kaydını AYNI kimlikle bulur (analysis_stored=True); ikinci tur yeni
    kayıt yazmaz; kararlar bu değişiklikten etkilenmez (mevcut `test_engine_decisions_unchanged_*` ile birlikte)."""
    from test_engine_v3 import _engine
    from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile
    from test_strategy_paper_engine_v1 import SYMS, _install
    monkeypatch.setenv("TRADINGBOT_CODE_SHA", "engine-test-sha")
    ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                          "chart_analysis": {"enabled": True, "keep_per_series": 20}}
    eng = _engine(tmp_path / "eng", monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    _install(eng, monkeypatch, btc_up=True, coin_above=True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    store = ChartAnalysisStore(st)
    idx = store.index()
    assert idx["schema_version"] == INDEX_SCHEMA
    for sym in SYMS:
        for book in ("strategy_paper", "strategy_paper_m2"):
            rec = store.latest(book, "USDM_PERP", sym, "4h")
            assert rec and rec["identity"]["market_type"] == "USDM_PERP" and rec["position"]["entry_avg"] > 0 and rec["source"]["bar_market"] == "SPOT"
            assert store.list(book, "SPOT", sym, "4h") == []
        main_rec = store.latest("main", "SPOT", sym, "4h") or store.latest("main", "USDM_PERP", sym, "4h")
        assert main_rec and main_rec["identity"]["market_type"] == main_rec["source"]["bar_market"]
        if main_rec["identity"]["market_type"] == "SPOT":
            assert not any(e["kind"] in ("entry", "stop", "target") and e.get("trade_id", "").startswith("main:F") for e in main_rec["elements"]), "SPOT kaydı futures defterini TAŞIMAZ"
    n1 = store.stats()["snapshots"]
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert store.stats()["snapshots"] == n1, "aynı bar + aynı defter/karar → yeni kayıt YOK"
    # panel: motorun çerçevelerinden mum dosyası → 'şimdi' görünümü motor kaydını AYNI kimlikle bulur
    data = tmp_path / "data"; data.mkdir()
    for sym, fr in eng.runner.last_frames.items():
        base = sym.split("/")[0]
        for tf in ("4h", "1d"):
            df = (fr or {}).get(tf)
            if df is not None:
                df[[c for c in ("timestamp", "open", "high", "low", "close", "volume") if c in df.columns]].to_csv(data / f"binanceusdm_{base}-USDT_{tf}.csv", index=False)
    c = _client(st, data)
    rec = store.latest("strategy_paper", "USDM_PERP", SYMS[0], "4h")
    j = c.get(f"/api/chart/{SYMS[0].split('/')[0]}?tf=4h&market=futures&book=strategy_paper&n=100").json()
    assert j["analysis_stored"] is True and j["analysis_origin"] == "store" and j["analysis"]["analysis_id"] == rec["analysis_id"], j.get("engine_record")
    assert j["engine_record"]["matches_now"] is True and j["position"]["entry"] == rec["position"]["entry_avg"] and "no_target" in _kinds(j)


# ====================================================================== kimlik / indirme adı (sunucu tarafı)
def test_snapshot_download_name_carries_symbol_tf_market_book_and_id(tmp_path: Path):
    st, data = _env(tmp_path)
    rec = _main_state_snapshot(st, data, as_of=NOW - 1_000)
    ChartAnalysisStore(st).save(rec)
    c = _client(st, data)
    r = c.get(f"/api/chart/BTC/snapshot/{rec['analysis_id']}")
    assert r.status_code == 200 and r.headers["content-disposition"] == "attachment; filename=BTC_USDT_4h_futures_main_%s.json" % rec["analysis_id"]
    assert json.loads(r.content)["analysis_id"] == rec["analysis_id"] and MARKET_TYPES["futures"] == "USDM_PERP"


# ====================================================================== BULGU 4 + kimlik: gerçek CHART_JS, node altında
NODE = shutil.which("node")
HARNESS = r"""
'use strict';
function El(tag){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.value='';this.innerHTML='';this.className='';this.checked=false;this.type='';}
El.prototype.appendChild=function(c){this.children.push(c);return c;};
El.prototype.addEventListener=function(t,f){(this.listeners[t]=this.listeners[t]||[]).push(f);};
El.prototype.fire=function(t){(this.listeners[t]||[]).forEach(function(f){f({});});};
El.prototype.querySelector=function(sel){var m=sel.match(/input\[data-(k|layer)="([^"]+)"\]/);if(!m)return null;
  function find(n){if(n.tag==='input'&&n.dataset[m[1]]===m[2])return n;for(var i=0;i<n.children.length;i++){var r=find(n.children[i]);if(r)return r;}return null;}return find(this);};
El.prototype.remove=function(){};El.prototype.click=function(){clicked.push(this);};
var clicked=[], created=[], draws=[], downloads=[], opened=[], pending=[];
var ids={};['ovbox','laybox','tf','mk','bk','nbars','hist','chart','srcline','explain','detail','reload','dl-png','dl-json'].forEach(function(i){ids[i]=new El(i);});
ids.tf.value='4h';ids.mk.value='futures';ids.bk.value='main';ids.nbars.value='300';ids.hist.value='';
ids.chart.on=function(){};ids.chart.removeAllListeners=function(){};
var document={getElementById:function(i){return ids[i]||null;},createElement:function(t){var e=new El(t);created.push(e);return e;},createTextNode:function(){return new El('#text');},body:new El('body')};
var window={__chartBase:'BTC',__chartTf:'4h',__chartMarket:'futures',__chartBook:'main',__tokenQs:'',innerWidth:1200,open:function(u){opened.push(u);}};
var Plotly={react:function(id,traces,layout){draws.push(layout.shapes.length);},downloadImage:function(id,o){downloads.push(o.filename);}};
var URL={createObjectURL:function(){return 'blob:x';},revokeObjectURL:function(){}};
function Blob(){}
function fetch(url){var p={url:url};p.promise=new Promise(function(res){p.resolve=function(body){res({json:function(){return Promise.resolve(body);}});};});pending.push(p);return p.promise;}
__CHART_JS__
function tick(){return new Promise(function(r){setImmediate(function(){setImmediate(r);});});}
function q(u){var o={path:u.split('?')[0]};(u.split('?')[1]||'').split('&').forEach(function(kv){var a=kv.split('=');o[decodeURIComponent(a[0])]=decodeURIComponent(a[1]||'');});return o;}
function chart(qq,over){var mt=qq.market==='futures'?'USDM_PERP':'SPOT';var aid=qq.analysis_id||('eph-'+qq.tf+'-'+qq.book);
  var d={req:qq.req,tf:qq.tf,market:qq.market,book:qq.book,t:[1,2,3],o:[1,1,1],h:[2,2,2],l:[0,0,0],c:[1,1,1],v:[1,1,1],overlays:{},panels:{},tf_ms:14400000,
    source:{file:'f.csv',market:qq.market,tf:qq.tf,last_bar_ts:3,age_s:1,stale:false},historical:!!qq.analysis_id,analysis_stored:!!qq.analysis_id,
    live:{as_of_ms:1758000000000,as_of:'2026-09-16T04:00:00Z',source:'futures_ledger.json'},engine_record:null,
    analysis:{analysis_id:aid,identity:{analysis_id:undefined,book_id:qq.book,timeframe:qq.tf,market_type:mt,as_of:'2026-09-15T10:00:00Z',as_of_ms:1757930400000,code_sha:'c0ffee00'},elements:[{id:'e',layer:'trades',kind:'entry',price:1,t0:1,label_tr:'x',decision_impact:'USED_IN_DECISION'}],explanation:[]}};
  return Object.assign(d,over||{});}
function history(qq,rows){return {req:qq.req,tf:qq.tf,market:qq.market,book:qq.book,rows:rows};}
var out={};
(async function(){
  await tick();
  var p0=pending.map(function(p){return q(p.url);});out.initial=p0;
  var h0=pending[0], c0=pending[1];
  h0.resolve(history(q(h0.url),[{analysis_id:'oldMainSnapshot',as_of_ms:1757900000000}]));await tick();
  c0.resolve(chart(q(c0.url)));await tick();
  out.afterInit={draws:draws.length,hist:ids.hist.innerHTML,last:window.__chartTest.last().analysis.analysis_id};
  // 1) gecmis kaydi sec (ana defter)
  ids.hist.value='oldMainSnapshot';ids.hist.fire('change');await tick();
  var c1=pending[2];out.histReq=q(c1.url);
  c1.resolve(chart(q(c1.url)));await tick();
  out.afterHist={draws:draws.length,src:ids.srcline.innerHTML,stem:window.__chartTest.fileStem(),lastId:window.__chartTest.last().analysis.analysis_id};
  ids['dl-json'].fire('click');out.jsonOpenStored=opened.slice();ids['dl-png'].fire('click');out.pngStored=downloads.slice();
  // 2) gecmis seciliyken deftere gec (T2): eski analysis_id yeni kapsama GITMEMELI, liste yeni deftere gitmeli
  ids.bk.value='strategy_paper';ids.bk.fire('change');await tick();
  out.afterBook={histValue:ids.hist.value,histHtml:ids.hist.innerHTML,reqs:pending.slice(3).map(function(p){return q(p.url);})};
  var h1=pending[3], c2=pending[4];
  // 3) yanit gelmeden dilimi degistir (1h): istekler sirayla; eski yanitlar TERS sirada ve GEC gelsin
  ids.tf.value='1h';ids.tf.fire('change');await tick();
  var h2=pending[5], c3=pending[6];out.afterTf=pending.slice(5).map(function(p){return q(p.url);});
  c2.resolve(chart(q(c2.url)));await tick();                       // eski kapsam (4h) yaniti once gelir: CIZILMEMELI
  out.staleChartDrawn={draws:draws.length,lastTf:window.__chartTest.last().tf};
  c3.resolve(chart(q(c3.url)));await tick();                       // guncel kapsam (1h)
  out.freshChart={draws:draws.length,lastTf:window.__chartTest.last().tf,lastBook:window.__chartTest.last().book,stem:window.__chartTest.fileStem()};
  h1.resolve(history(q(h1.url),[{analysis_id:'stale4h',as_of_ms:1}]));await tick();   // eski kapsam listesi gec gelir: SECENEKLERI EZMEMELI
  out.staleHist=ids.hist.innerHTML;
  h2.resolve(history(q(h2.url),[{analysis_id:'t2snap1h',as_of_ms:2}]));await tick();
  out.freshHist={html:ids.hist.innerHTML,value:ids.hist.value};
  // 4) sunucu baska kimlik dondururse (defter uyusmaz) cizilmez
  ids.nbars.value='100';ids.nbars.fire('change');await tick();
  var c4=pending[7];c4.resolve(chart(q(c4.url),{analysis:{analysis_id:'wrong',identity:{book_id:'main',timeframe:'1h',market_type:'USDM_PERP'},elements:[],explanation:[]}}));await tick();
  out.wrongIdentity={draws:draws.length,lastId:window.__chartTest.last().analysis.analysis_id};
  // 5) gecici (kaydedilmemis) analizde JSON indirme blob ile, ad canli-...
  ids['dl-json'].fire('click');await tick();
  var anchors=created.filter(function(e){return e.tag==='a';});out.ephemeralJson={opened:opened.length,download:anchors.length?anchors[anchors.length-1].download:null,stem:window.__chartTest.fileStem()};
  // 6) yeniden yukle: secili gecmis korunur
  ids.hist.value='t2snap1h';ids.reload.fire('click');await tick();
  var h3=pending[8], c5=pending[9];out.reloadReqs=[q(h3.url),q(c5.url)];
  h3.resolve(history(q(h3.url),[{analysis_id:'t2snap1h',as_of_ms:2},{analysis_id:'t2snap1h-b',as_of_ms:3}]));await tick();
  out.reloadHist={value:ids.hist.value,html:ids.hist.innerHTML};
  console.log(JSON.stringify(out));
})().catch(function(e){console.log(JSON.stringify({error:String(e&&e.stack||e)}));});
"""


@pytest.mark.skipif(NODE is None, reason="node yok: CHART_JS davranış testi atlandı")
def test_f4_chart_js_scope_change_history_reset_and_late_or_reordered_responses(tmp_path: Path):
    """2e31926: değişim işleyicisi loadHistory() sonra load() çağırıyor; tf/market/book ancak load() içinde güncelleniyor →
    T2'ye geçişte liste isteği ana deftere, grafik isteği T2'ye ESKİ ana defter analysis_id'siyle gidiyordu; history fetch'inde geç
    yanıt koruması yoktu; kimlik identity.analysis_id'den okunuyor (kökte) → kaynak satırı/indirme adı boş/'canli'. Bu test gerçek
    CHART_JS metnini küçük bir DOM + kontrollü fetch ile node altında çalıştırır."""
    js = tmp_path / "harness.js"
    js.write_text(HARNESS.replace("__CHART_JS__", CHART_JS), encoding="utf-8")
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    assert [p["path"] for p in out["initial"]] == ["/api/chart/BTC/history", "/api/chart/BTC"] and out["initial"][1]["book"] == "main"
    assert out["afterInit"]["draws"] == 1 and "oldMainSnapshot" in out["afterInit"]["hist"]
    # geçmiş seçimi: istek analysis_id taşır; kaynak satırı ve indirme adı GERÇEK kimliği taşır (kök alan)
    assert out["histReq"]["analysis_id"] == "oldMainSnapshot" and out["histReq"]["book"] == "main"
    assert out["afterHist"]["lastId"] == "oldMainSnapshot" and "analiz oldMainSnapshot" in out["afterHist"]["src"] and "GEÇMİŞ ANALİZ" in out["afterHist"]["src"]
    assert "motor kaydı" in out["afterHist"]["src"] and "defter main" in out["afterHist"]["src"] and "futures 4h" in out["afterHist"]["src"]
    assert out["afterHist"]["stem"] == "BTC_4h_futures_main_oldMainSnapshot" and out["pngStored"] == ["BTC_4h_futures_main_oldMainSnapshot"]
    assert out["jsonOpenStored"] == ["/api/chart/BTC/snapshot/oldMainSnapshot"]
    # defter değişimi: geçmiş seçimi temizlenir; liste VE grafik isteği yeni deftere; eski analysis_id gönderilmez
    assert out["afterBook"]["histValue"] == "" and "oldMainSnapshot" not in out["afterBook"]["histHtml"]
    reqs = out["afterBook"]["reqs"]
    assert [p["path"] for p in reqs] == ["/api/chart/BTC/history", "/api/chart/BTC"]
    assert reqs[0]["book"] == "strategy_paper" and reqs[1]["book"] == "strategy_paper" and "analysis_id" not in reqs[1]
    # dilim değişimi + geç/ters sırada yanıtlar: eski kapsam yanıtı çizilmez, eski liste seçenekleri ezmez
    assert all(p["tf"] == "1h" and p["book"] == "strategy_paper" for p in out["afterTf"])
    assert out["staleChartDrawn"]["draws"] == 2 and out["staleChartDrawn"]["lastTf"] == "4h", "eski (4h) yanıt yeni seçimi EZMEDİ"
    assert out["freshChart"]["draws"] == 3 and out["freshChart"]["lastTf"] == "1h" and out["freshChart"]["lastBook"] == "strategy_paper"
    assert out["freshChart"]["stem"] == "BTC_1h_futures_strategy_paper_canli-202609151000"
    assert "stale4h" not in out["staleHist"] and "t2snap1h" in out["freshHist"]["html"] and out["freshHist"]["value"] == ""
    # sunucu başka kimlik döndürürse çizilmez; geçici analiz JSON'u blob ile ve 'canli-' adıyla iner
    assert out["wrongIdentity"]["draws"] == 3 and out["wrongIdentity"]["lastId"] == "eph-1h-strategy_paper"
    assert out["ephemeralJson"]["opened"] == 1 and out["ephemeralJson"]["download"] == "BTC_1h_futures_strategy_paper_canli-202609151000.json"
    # yeniden yükle: seçili geçmiş kimliği korunur, istekler aynı kapsamda
    assert out["reloadReqs"][0]["book"] == "strategy_paper" and out["reloadReqs"][1]["analysis_id"] == "t2snap1h"
    assert out["reloadHist"]["value"] == "t2snap1h"


def test_chart_js_reads_analysis_id_from_root_and_never_from_identity():
    assert "identity.analysis_id" not in CHART_JS and "A.analysis_id" in CHART_JS
    assert "a.analysis_id" not in CHART_JS.replace("A.analysis_id", "")
