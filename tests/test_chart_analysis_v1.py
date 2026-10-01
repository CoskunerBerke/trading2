# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — gorunurluk katmani testleri.

Kapsam: (1) uc dogrulanmis grafik kusuru; (2) piyasa/defter ayrimi; (3) hedefsiz pozisyon; (4) LONG/SHORT ve
kapanmis islem; (5) eksik/bayat veri; (6) gelecek mumlar gelince gecmis analizin AYNI kalmasi; (7) gec gelen istek
sozlesmesi (req yankisi); (8) grafik okumasinin deftere YAZMAMASI; (9) ana bot/T2/M2 kararlarinin bu degisiklikle
DEGISMEMESI (motor turu, acik/kapali karsilastirma); (10) saklama tekillestirme/yeniden yazmama/budama.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from test_chart_patterns_v1 import DOUBLE_BOTTOM_PATH, _bars  # noqa: E402
from tradingbot.chart_analysis import (INVALID, OBSERVATION_ONLY, UNCONFIRMED, USED_IN_DECISION, build_snapshot,  # noqa: E402
                                       closed_bars_at, level_elements, pivots, trade_elements, trendline_elements)
from tradingbot.chart_analysis_store import ChartAnalysisStore  # noqa: E402
from tradingbot.chart_patterns import ChartPatternConfig, detect_chart_patterns  # noqa: E402
from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402
from tradingbot.dashboard.candles import CandleSource  # noqa: E402

H4 = 14_400_000
T0 = 1_700_000_000_000
GATES = {"candle_mode": "ENFORCE", "candle_variant": "c3_4h_veto", "chart_mode": "SHADOW", "chart_variant": "p2_4h_veto",
         "regime_mode": "ENFORCE", "regime_variant": "r1_long_only_uptrend", "chart_fresh_within": 3}


def _series(n=400, seed=3, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"timestamp": T0 + np.arange(n) * H4, "open": close * 0.998, "high": close * 1.012, "low": close * 0.988, "close": close, "volume": 5.0})


def _bars_ts(path=DOUBLE_BOTTOM_PATH):
    bars = _bars(path)
    for i, b in enumerate(bars):
        b["timestamp"] = T0 + i * H4
        b["volume"] = 1.0
    return bars


def _daily(n=260, above=True):
    D = 86_400_000
    return [{"timestamp": 1_690_000_000_000 + i * D, "open": 100, "high": 101, "low": 99, "close": 100 + i * 0.02,
             "ema200": (95.0 if above else 105.0), "atr14": 2.0} for i in range(n)]


BTC_UP = [{"timestamp": 0, "close": 100.0, "ema200": 90.0}]


def _snap(bars, *, book=("strategy_paper", "t2_trend_regime"), as_of=None, position=None, history=None, plan=None, decision=None, gates=None, daily=None):
    return build_snapshot(symbol="SOL/USDT", market_type="USDM_PERP", timeframe="4h", tf_ms=H4, book={"book_id": book[0], "name": book[1], "atr_mult": 3.0},
                          bars=bars, as_of_ms=as_of if as_of is not None else bars[-1]["timestamp"] + H4, daily_rows=daily if daily is not None else _daily(),
                          btc_daily_rows=BTC_UP, gates=gates or GATES, decision=decision, plan=plan, position=position, history=history or [],
                          entry_features=None, mark_price=float(bars[-1]["close"]), cfg={"swing_lookback": 3}, code="deadbeef", cfg_hash="cfg1")


# ------------------------------------------------------------------ (1) kusur: futures verisi spot dosyasina DUSMEZ
def test_candle_source_is_market_strict(tmp_path: Path):
    data = tmp_path / "data"; data.mkdir()
    _series().to_csv(data / "tv-binance_BTC-USDT_4h.csv", index=False)
    cs = CandleSource(data)
    assert cs.find("BTC", "4h", "spot") is not None
    assert cs.find("BTC", "4h", "futures") is None, "yalniz spot dosyasi varken futures istegi VERI YOK donmeli"
    assert cs.load("BTC", "4h", "futures") is None
    info = cs.source_info("BTC", "4h", "futures")
    assert info["missing"] is True and info["file"] is None
    _series(seed=9).to_csv(data / "binanceusdm_BTC-USDT_4h.csv", index=False)
    assert cs.find("BTC", "4h", "futures").name == "binanceusdm_BTC-USDT_4h.csv"
    assert cs.find("BTC", "1h", "spot") is None, "baska zaman dilimiyle doldurma YOK"
    # parquet adayi: sembol / piyasa / dilim KESIN eslesme
    (data / "candles" / "spot").mkdir(parents=True)
    pm = cs._parquet_matches
    assert pm(data / "candles/spot/BTC-USDT_4h.parquet", "BTC", ("4h", "240"), "spot")
    assert not pm(data / "candles/spot/BTC-USDT_4h.parquet", "BTC", ("4h", "240"), "futures"), "spot klasoru futures istegine verilmez"
    assert not pm(data / "candles/spot/XBTC-USDT_4h.parquet", "BTC", ("4h", "240"), "spot"), "kismi sembol eslesmesi yok"
    assert not pm(data / "candles/spot/BTC-USDT_1h.parquet", "BTC", ("4h", "240"), "spot")
    assert pm(data / "candles/futures/BTCUSDT_240.parquet", "BTC", ("4h", "240"), "futures")


# ------------------------------------------------------------------ (2) kusur: spot grafigi futures pozisyon/planini TASIMAZ
def _dash_state(tmp_path: Path, *, spot_close=101.0, futures_entry=205.0, futures_stop=190.0):
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(); data.mkdir()
    n = 400
    base = _series(n, seed=1, start=spot_close)
    spot = base.copy(); spot["close"] = spot_close; spot["open"] = spot_close; spot["high"] = spot_close * 1.01; spot["low"] = spot_close * 0.99
    spot.to_csv(data / "tv-binance_BTC-USDT_4h.csv", index=False)
    fut = base.copy(); fut["close"] = 200.0; fut["open"] = 200.0; fut["high"] = 202.0; fut["low"] = 198.0
    fut.to_csv(data / "binanceusdm_BTC-USDT_4h.csv", index=False)
    heads = {"generated_at": "2026-01-01T00:00:00+00:00", "run_id": "r1", "heads": [
        {"symbol": "BTC/USDT", "market_type": "futures", "verdict": "FUTURES_LONG", "direction": "LONG", "no_trade_reason": "", "p_win": 0.5,
         "spot_plan": None, "futures_plan": {"valid": True, "entry": 204.0, "stop": futures_stop, "targets": [230.0], "direction": "LONG", "entry_type": "market"},
         "specialist_reports": [], "consensus": {}, "dissent": [], "vetoes": [], "factor_scores": [], "data_freshness": {}}], "chief": {}}
    (st / "coin_heads.json").write_text(json.dumps(heads), encoding="utf-8")
    led = {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100", "equity": "100", "total_fees": "0",
           "positions": {"BTC/USDT": {"id": "F00016", "symbol": "BTC/USDT", "side": "LONG", "qty": "0.1", "entry_avg": str(futures_entry), "stop": str(futures_stop),
                                      "targets": ["230"], "opened_at": "2026-01-01T00:00:00+00:00", "leverage": 2, "isolated_margin": "10"}},
           "history": [{"id": "F00001", "symbol": "BTC/USDT", "side": "SHORT", "entry": 210.0, "exit_price": 205.0, "opened_at": "2025-12-01T00:00:00+00:00",
                        "closed_at": "2025-12-03T00:00:00+00:00", "exit_reason": "TP1", "net_pnl": 0.4, "r_multiple": 0.8}]}
    (st / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps({"cash": 50.0, "starting_equity": 50.0, "positions": {}, "history": []}), encoding="utf-8")
    for fn, d in (("killswitch.json", {"state": "ARMED", "since": "", "reasons": [], "audit": []}), ("mode.json", {"mode": "PAPER", "history": []}),
                  ("risk.json", {"profile": {"name": "PAPER_RESEARCH"}, "exposure": {"equity": 100},
                                 "last_decisions": [{"symbol": "BTC/USDT", "verdict": "FUTURES_LONG", "block_code": "NEGATIVE_NET_EDGE", "at": "2026-01-01T00:00:00+00:00",
                                                     "candle_confirmation": {"blocks": False, "verdict": {"reason": None}},
                                                     "regime_gate": {"blocks": False, "regime": "UP", "verdict": {"reason": None}}}]})):
        (st / fn).write_text(json.dumps(d), encoding="utf-8")
    return st, data


def test_api_candles_and_chart_do_not_leak_futures_into_spot(tmp_path: Path):
    st, data = _dash_state(tmp_path)
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    j = c.get("/api/candles/BTC?tf=4h&n=100&market=spot").json()
    assert j["plan"] == {} and j["position"] == {}, "spot yaniti futures giris/stop TASIMAMALI"
    assert abs(j["c"][-1] - 101.0) < 1e-6
    jf = c.get("/api/candles/BTC?tf=4h&n=100&market=futures").json()
    assert jf["position"]["entry"] == 205.0 and jf["position"]["stop"] == 190.0 and jf["plan"]["stop"] == 190.0
    # yeni uc: ayni ayrim + analiz katmani
    a = c.get("/api/chart/BTC?tf=4h&n=100&market=spot&book=main&req=7").json()
    assert a["req"] == "7" and a["position"] == {} and a["plan"] == {}
    kinds = {e["kind"] for e in (a["analysis"] or {}).get("elements", [])}
    assert "entry" not in kinds and "plan_entry" not in kinds
    b = c.get("/api/chart/BTC?tf=4h&n=100&market=futures&book=main&req=8").json()
    kinds = {e["kind"]: e for e in b["analysis"]["elements"]}
    assert kinds["entry"]["price"] == 205.0 and kinds["stop"]["price"] == 190.0 and kinds["plan_entry"]["price"] == 204.0
    assert kinds["trade_entry"]["side"] == "SHORT" and kinds["trade_exit"]["price"] == 205.0 and kinds["trade_entry"]["trade_id"] == "main:F00001"
    assert any(l["k"] == "karar" and "NEGATIVE_NET_EDGE" in l["v"] for l in b["analysis"]["explanation"])


# ------------------------------------------------------------------ (3) kusur: hedefsiz acik LONG PNG'de cizilir
def test_render_signal_chart_draws_targetless_position(tmp_path: Path, monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.axes import Axes
    from tradingbot.charts import render_signal_chart
    texts: list[str] = []
    orig_text = Axes.text

    def spy(self, *a, **k):
        if len(a) >= 3:
            texts.append(str(a[2]))
        return orig_text(self, *a, **k)
    monkeypatch.setattr(Axes, "text", spy)
    df = _series(140).set_index(pd.to_datetime(_series(140)["timestamp"], unit="ms"))
    out = render_signal_chart(df, tmp_path / "x.png", title="SOL 4h", plan=None, levels=None,
                              position={"side": "LONG", "entry": 100.0, "stop": 95.0})          # target1/target2 YOK
    assert out.exists() and out.stat().st_size > 1000
    joined = " ".join(texts)
    assert "GİRİŞ 100" in joined and "STOP 95" in joined and "TP YOK" in joined and "TP1" not in joined
    assert "BEKLE" not in joined


# ------------------------------------------------------------------ analiz geometrisi
def test_pivots_confirmed_before_as_of_and_pending_flagged():
    """Teyit zamani = teyit barinin KAPANISI (bulgu #5): hicbir pivot analiz anindan (as_of) sonra 'bilinmis' olamaz;
    2e31926'nin eski testi 'confirmed_at <= son barin ACILISI' diyerek bir mum erken tanimi sabitliyordu."""
    bars = _bars_ts()
    as_of = bars[-1]["timestamp"] + H4
    s = _snap(bars, as_of=as_of)
    assert s["pivots"]["confirmed"], "cift dip serisinde teyitli pivot beklenir"
    for p in s["pivots"]["confirmed"]:
        assert p["confirmed_at"] is not None and p["confirmed_at"] <= as_of
        assert p["confirmed_at"] == p["confirmed_bar_open"] + H4 and p["confirmed_at"] >= p["timestamp"] + 4 * H4   # lookback 3: i+3 kapanis
    pend = [e for e in s["elements"] if e["kind"] == "pivot_pending"]
    assert pend and pend[0]["decision_impact"] == UNCONFIRMED and pend[0]["confirmed_at"] is None


def test_zones_come_from_real_clusters_and_need_two_anchors():
    bars = _bars_ts()
    piv = pivots(bars, lookback=3, tf_ms=H4)
    zones = [e for e in level_elements(bars, piv, atr=1.0, tolerance_atr=0.6, timeframe="4h", last_close=bars[-1]["close"], tf_ms=H4) if e["layer"] == "zones"]
    assert zones, "cift dip serisinde en az bir kume beklenir"
    for z in zones:
        assert z["n_anchors"] >= 2 and z["lower"] <= z["upper"]
        assert all(z["lower"] <= a["price"] <= z["upper"] for a in z["anchors"])
        assert z["decision_impact"] == OBSERVATION_ONLY and z["confirmed_at"] is not None
    assert not [e for e in level_elements(bars, piv, atr=None, tolerance_atr=0.6, timeframe="4h", last_close=100.0, tf_ms=H4) if e["layer"] == "zones"], "ATR yoksa bolge YOK"


def test_trendline_connects_two_confirmed_pivots_and_detects_break():
    closes = []
    for cyc in range(4):                       # yukselen dipler: her dongu bir oncekinden yuksek
        closes += [100 + cyc * 3 + x for x in (2, 4, 6, 4, 2, 0, 1, 3)]
    intact = _bars_ts(closes + [117, 118, 119])
    piv = pivots(intact, lookback=3, tf_ms=H4)
    tl = [e for e in trendline_elements(intact, piv, touch_tolerance_pct=0.3, timeframe="4h", tf_ms=H4) if e["kind"] == "trend_support"]
    assert tl and len(tl[0]["anchors"]) == 2 and tl[0]["slope_per_bar"] > 0 and tl[0]["status"] == "INTACT" and tl[0]["decision_impact"] == OBSERVATION_ONLY
    assert tl[0]["t0"] < tl[0]["t1"] and tl[0]["anchors"][0]["confirmed_at"] is not None
    broken = _bars_ts(closes + [117, 105, 96, 94, 93])
    piv2 = pivots(broken, lookback=3, tf_ms=H4)
    tl2 = [e for e in trendline_elements(broken, piv2, touch_tolerance_pct=0.3, timeframe="4h", tf_ms=H4) if e["kind"] == "trend_support"]
    assert tl2 and tl2[0]["status"] == "BROKEN" and tl2[0]["breaks"] and tl2[0]["decision_impact"] == INVALID


def test_pattern_lines_use_detector_anchors_and_mode_decides_impact():
    bars = _bars_ts()
    det = detect_chart_patterns(bars, ChartPatternConfig())
    db = [p for p in det["patterns"] if p["pattern"] == "DOUBLE_BOTTOM"][0]
    assert len(db["anchors"]) == 2 and db["geometry"] and db["geometry"][0]["t0"] < db["geometry"][0]["t1"] and db["break_ts"] == db["geometry"][0]["t1"]
    shadow = [e for e in _snap(bars)["elements"] if e["layer"] == "patterns"]
    assert shadow and all(e["decision_impact"] == OBSERVATION_ONLY for e in shadow) and shadow[0]["anchors"][0]["role"] == "dip1"
    enforce = [e for e in _snap(bars, gates={**GATES, "chart_mode": "ENFORCE"})["elements"] if e["layer"] == "patterns"]
    assert enforce and enforce[0]["decision_impact"] == USED_IN_DECISION
    flat = _bars_ts([100.0 + 0.1 * ((i % 3) - 1) for i in range(60)])
    assert any(l["k"] == "formasyonlar" and "bulunamadı" in l["v"] for l in _snap(flat)["explanation"])


# ------------------------------------------------------------------ gecmis analiz AYNI kalir; defter ayrimi; TP yok; LONG/SHORT
def test_snapshot_is_point_in_time_and_future_bars_do_not_change_it():
    bars = _bars_ts()
    cut = len(bars) - 5
    as_of = bars[cut - 1]["timestamp"] + H4
    a = _snap(closed_bars_at(bars, as_of_ms=as_of, tf="4h"), as_of=as_of)
    b = _snap(closed_bars_at(bars[:cut], as_of_ms=as_of, tf="4h"), as_of=as_of)
    assert a["analysis_id"] == b["analysis_id"] and a["elements"] == b["elements"]
    assert a["identity"]["last_closed_bar"]["timestamp"] == bars[cut - 1]["timestamp"]
    full = _snap(bars)
    assert full["analysis_id"] != a["analysis_id"]


def test_books_do_not_share_trade_ids_and_targetless_position_says_no_tp():
    bars = _bars_ts()
    pos = {"id": "F00001", "symbol": "SOL/USDT", "side": "LONG", "qty": 0.2, "entry_avg": 101.3, "stop": 87.27, "targets": [], "opened_at": "2023-11-01T00:00:00+00:00"}
    t2 = _snap(bars, book=("strategy_paper", "t2_trend_regime"), position=pos)
    m2 = _snap(bars, book=("strategy_paper_m2", "m2_tsmom28"), position=pos)
    ids_t2 = {e["id"] for e in t2["elements"] if e["layer"] == "trades"}
    ids_m2 = {e["id"] for e in m2["elements"] if e["layer"] == "trades"}
    assert "pos_entry:strategy_paper:F00001" in ids_t2 and "pos_entry:strategy_paper_m2:F00001" in ids_m2 and not (ids_t2 & ids_m2)
    assert t2["analysis_id"] != m2["analysis_id"]
    kinds = {e["kind"] for e in t2["elements"] if e["layer"] == "trades"}
    assert "no_target" in kinds and "target" not in kinds
    assert any("TP yok" in l["v"] for l in t2["explanation"]) and any("OTOMATİK KAPANMAZ" in l["v"] for l in t2["explanation"])
    assert any(l["k"] == "kural" and "M2" in l["v"] and "28 gün" in l["v"] for l in m2["explanation"])
    assert any(e["kind"] == "rule_reference" and "28g" in e["label_tr"] for e in m2["elements"])


def test_long_short_markers_and_realized_vs_open_pnl():
    bars = _bars_ts()
    hist = [{"id": "F1", "symbol": "SOL/USDT", "side": "LONG", "entry": 100.0, "exit_price": 104.0, "opened_at": bars[3]["timestamp"], "closed_at": bars[8]["timestamp"],
             "exit_reason": "TP1", "net_pnl": 0.8, "r_multiple": 0.8}]
    pos = {"id": "F2", "symbol": "SOL/USDT", "side": "SHORT", "qty": 1.0, "entry_avg": 110.0, "stop": 115.0, "targets": [100.0], "opened_at": bars[20]["timestamp"]}
    els = trade_elements(book_id="main", position=pos, history=hist, plan=None, as_of_ms=bars[-1]["timestamp"], mark_price=108.0)
    k = {e["kind"]: e for e in els}
    assert k["trade_entry"]["side"] == "LONG" and k["trade_exit"]["net_pnl"] == 0.8 and k["trade_entry"]["trade_id"] == "main:F1"
    assert k["entry"]["side"] == "SHORT" and "SHORT" in k["entry"]["label_tr"] and k["target"]["label_tr"].endswith("(2.00R)")
    assert "+2" in k["mark"]["label_tr"], "SHORT acik K/Z isaret fiyatiyla: (110-108)*1 = +2"
    # gecmis an: kapanis ILERIDE ise cikis isareti YOK, giris var
    early = trade_elements(book_id="main", position=None, history=hist, plan=None, as_of_ms=bars[5]["timestamp"], mark_price=None)
    assert {e["kind"] for e in early} == {"trade_entry"}


# ------------------------------------------------------------------ saklama: tekillestirme, yeniden yazmama, budama
def test_store_dedups_never_rewrites_and_prunes(tmp_path: Path):
    st = ChartAnalysisStore(tmp_path, keep_per_series=2)
    bars = _bars_ts()
    s1 = _snap(bars)
    r = st.save(s1); assert r["written"] is True
    f = tmp_path / "chart_analysis" / s1["identity"]["book_id"] / "USDM_PERP" / "SOL_USDT_4h"   # v2 yerlesim: piyasa yolda
    files = sorted(f.glob("*.json")); assert len(files) == 1
    before = files[0].read_bytes(); mt = files[0].stat().st_mtime_ns
    s1b = dict(s1); s1b["elements"] = []                      # ayni kimlikle farkli icerik -> ESKI kayit korunur
    assert st.save(s1b)["written"] is False and files[0].read_bytes() == before and files[0].stat().st_mtime_ns == mt
    for k in (1, 2):
        s = _snap(bars[: len(bars) - k]); st.save(s)
    rows = st.list("strategy_paper", "USDM_PERP", "SOL/USDT", "4h")
    assert len(rows) == 2 and len(list(f.glob("*.json"))) == 2, "keep_per_series=2: en eski silinir"
    assert st.load(rows[-1]["analysis_id"])["analysis_id"] == rows[-1]["analysis_id"] and st.load("../x") is None
    assert st.stats()["snapshots"] == 2


# ------------------------------------------------------------------ panel: eksik/bayat veri, gecmis secimi, salt okuma
def test_api_chart_missing_stale_history_and_read_only(tmp_path: Path):
    st, data = _dash_state(tmp_path)
    os.remove(data / "binanceusdm_BTC-USDT_4h.csv")
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/api/chart/BTC?market=futures&book=main")
    assert r.status_code == 404 and r.json()["source"]["missing"] is True and "doldurulmadı" in r.json()["error"]
    assert c.get("/api/chart/BTC?market=spot&book=yok").status_code == 404
    snap_before = {p.name: p.stat().st_mtime_ns for p in st.iterdir() if p.is_file()}
    j = c.get("/api/chart/BTC?market=spot&book=main&n=100").json()
    assert j["source"]["stale"] is True and j["source"]["missing"] is False, "2023 tarihli veri BAYAT isaretlenir"
    assert j["analysis_origin"] in ("panel-ephemeral", "panel-cache") and j["historical"] is False
    # panel hicbir state dosyasina dokunmadi, analiz kaydi YAZMADI
    assert {p.name: p.stat().st_mtime_ns for p in st.iterdir() if p.is_file()} == snap_before
    assert not (st / "chart_analysis").exists()
    # gecmis analiz: motor kaydi gibi sakla, sec, mumlar o ana kadar kesilir
    bars = [{"timestamp": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(cl)} for t, o, h, l, cl in
            zip(*[CandleSource(data).load("BTC", "4h", "spot")[k] for k in ("timestamp", "open", "high", "low", "close")])]
    cut = bars[300]["timestamp"] + H4
    hist_snap = build_snapshot(symbol="BTC/USDT", market_type="SPOT", timeframe="4h", tf_ms=H4, book={"book_id": "main", "name": "main"},
                               bars=closed_bars_at(bars, as_of_ms=cut, tf="4h"), as_of_ms=cut, daily_rows=[], btc_daily_rows=[], gates=GATES,
                               decision=None, plan=None, position=None, history=[], entry_features=None, mark_price=None, cfg={})
    ChartAnalysisStore(st).save(hist_snap)
    rows = c.get("/api/chart/BTC/history?market=spot&book=main").json()["rows"]
    assert rows and rows[0]["analysis_id"] == hist_snap["analysis_id"]
    h = c.get("/api/chart/BTC?market=spot&book=main&n=600&analysis_id=%s&req=3" % hist_snap["analysis_id"]).json()
    assert h["historical"] is True and h["req"] == "3" and max(h["t"]) <= bars[300]["timestamp"]
    assert c.get("/api/chart/BTC?market=spot&book=strategy_paper&analysis_id=%s" % hist_snap["analysis_id"]).status_code in (404,)
    dl = c.get("/api/chart/BTC/snapshot/%s" % hist_snap["analysis_id"])
    assert dl.status_code == 200 and "attachment" in dl.headers.get("content-disposition", "") and json.loads(dl.content)["analysis_id"] == hist_snap["analysis_id"]
    assert c.get("/coin/BTC?market=spot&book=main").status_code == 200


# ------------------------------------------------------------------ motor: kararlar DEGISMEZ, kayit tekil
def test_engine_decisions_unchanged_and_snapshots_deduped(tmp_path: Path, monkeypatch):
    from test_engine_v3 import _engine
    from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile
    from test_strategy_paper_engine_v1 import SYMS, _install

    from test_chart_analysis_v1_fixes import install_perp_frames

    def run(sub: str, enabled: bool):
        ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                                 "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                              "chart_analysis": {"enabled": enabled, "keep_per_series": 5},
                              "entry_universe": {"enabled": True, "symbols": list(SYMS)}}   # perp cerceve istenir (provenans USDM_PERP)
        eng = _engine(tmp_path / sub, monkeypatch, ov, symbols=2, equity=EQUITY)
        _force_triggers(monkeypatch, False)
        _install(eng, monkeypatch, btc_up=True, coin_above=True)
        install_perp_frames(eng, monkeypatch)      # 2026-09-16 #3: kagit defter kaydi yalniz dogrulanmis USDM_PERP cerceveyle yazilir
        eng.tour(do_scan=False, obsidian=False, charts=False)
        st = eng.cfg.state_path
        funnel = json.loads((st / "decision_funnel.json").read_text(encoding="utf-8")).get("run")
        books = {b.key: sorted(b.ledger.positions) for b in eng.strategy_books}
        return eng, funnel, sorted(eng.ledger2.positions), books
    e_off, f_off, p_off, b_off = run("off", False)
    e_on, f_on, p_on, b_on = run("on", True)
    assert f_on == f_off and p_on == p_off and b_on == b_off == {"strategy_paper": sorted(SYMS), "strategy_paper_m2": sorted(SYMS)}
    assert not (e_off.cfg.state_path / "chart_analysis").exists()
    store = ChartAnalysisStore(e_on.cfg.state_path)
    stats = store.stats()
    assert stats["snapshots"] >= 3 * 2 and (e_on.cfg.state_path / "chart_analysis" / "config.json").exists()
    cfgj = json.loads((e_on.cfg.state_path / "chart_analysis" / "config.json").read_text(encoding="utf-8"))
    assert cfgj["gates"]["regime_mode"] and cfgj["timeframe"] == "4h"
    latest = store.latest("strategy_paper", "USDM_PERP", SYMS[0], "4h")
    assert latest["position"]["entry_avg"] > 0 and latest["entry_features"]["stop_at_entry"] is not None
    assert latest["rule_state"]["ok"] and latest["rule_state"]["above"] is True and latest["rule_state"]["regime"] == "UP"
    # ayni barlar, ayni karar -> ikinci tur HIC yeni kayit yazmaz
    e_on.tour(do_scan=False, obsidian=False, charts=False)
    assert store.stats()["snapshots"] == stats["snapshots"]
