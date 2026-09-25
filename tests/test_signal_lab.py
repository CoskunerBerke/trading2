# -*- coding: utf-8 -*-
"""Sinyal laboratuvarı: gelecek bilgisi yok, adım adımla aynı olaylar, maliyetli ve kötümser işlem simülasyonu."""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pandas as pd

from tradingbot import signal_lab as L

STEP = 3_600_000
T0 = 1_700_000_000_000 - 1_700_000_000_000 % STEP


def synth(n: int, seed: int = 1, sub: int = 12) -> pd.DataFrame:
    """Gerçek bir fiyat YOLU: her mum `sub` alt adımlı rastgele yürüyüş; fitiller yolun parçası (sıfır avantajlı piyasa)."""
    rnd = np.random.default_rng(seed)
    px, rows = 100.0, []
    for i in range(n):
        path = px * np.exp(np.cumsum(rnd.normal(0, 0.01 / np.sqrt(sub), sub)))
        c = float(path[-1])
        rows.append({"timestamp": T0 + i * STEP, "open": px, "high": max(px, float(path.max())), "low": min(px, float(path.min())),
                     "close": c, "volume": float(rnd.uniform(50, 150)), "close_time": T0 + (i + 1) * STEP - 1})
        px = c
    return pd.DataFrame(rows)


def _flat(n=40, px=100.0):
    return {k: np.full(n, px) for k in ("open", "high", "low", "close")}


def test_simulation_is_costed_and_pessimistic():
    cfg = L.LabConfig(max_hold_bars=5)
    atr = np.full(40, 2.0)
    arr = _flat()
    arr["high"][11] = 104.5                                    # hedef (2R = 104) vuruldu
    ev = L.Event("X", "1h", "t", "T", L.LONG, 10, 0, stop=98.0)
    assert L.simulate(ev, arr, atr, cfg) == "" and ev.exit_reason == "TARGET"
    cost = (100 + 104) * cfg.cost_per_side / 2.0
    assert abs(ev.r - (2.0 - cost)) < 1e-9, ev.r
    assert abs(ev.cost_r - cost) < 1e-9, "maliyetin R payı ayrıca kaydedilir"
    arr = _flat()
    arr["high"][11], arr["low"][11] = 104.5, 97.0              # aynı barda ikisi de → STOP (iyimserlik yok)
    ev = L.Event("X", "1h", "t", "T", L.LONG, 10, 0, stop=98.0)
    L.simulate(ev, arr, atr, cfg)
    assert ev.exit_reason == "STOP" and ev.r < -1.0
    arr = _flat()
    arr["open"][12], arr["low"][12] = 95.0, 94.0               # boşlukla stop altı açılış → açılıştan çıkış
    ev = L.Event("X", "1h", "t", "T", L.LONG, 10, 0, stop=98.0)
    L.simulate(ev, arr, atr, cfg)
    assert ev.exit_reason == "STOP" and ev.r < -2.4
    ev = L.Event("X", "1h", "t", "T", L.LONG, 10, 0, stop=98.0, trigger=97.0)   # giriş tetikten 3 > 1 ATR uzak
    assert L.simulate(ev, _flat(), atr, cfg) == "CHASE"
    ev = L.Event("X", "1h", "t", "T", L.SHORT, 10, 0, stop=102.0)
    assert L.simulate(ev, _flat(), atr, cfg) == "" and ev.exit_reason == "TIME" and ev.r < 0   # yalnız maliyet


def test_catalog_scan_with_stride_matches_bar_by_bar():
    df = synth(700)
    cfg = L.LabConfig()
    key = lambda evs: {(e.name, e.side, e.i, round(e.stop, 8)) for e in evs}  # noqa: E731
    one = key(L.catalog_events(df, "X/USDT", "1h", dataclasses.replace(cfg, stride=1)))
    three = key(L.catalog_events(df, "X/USDT", "1h", cfg))
    assert one and one == three


def test_no_lookahead_signals_and_context_ignore_future_bars():
    df = synth(760, seed=3)
    cfg = L.LabConfig()
    cut = 700
    a, _ = L.process_series(df.iloc[:cut].reset_index(drop=True), "X/USDT", "1h", cfg)
    fut = df.copy()
    fut.loc[cut:, ["open", "high", "low", "close"]] *= 3.0     # gelecek tamamen farklı
    b, _ = L.process_series(fut, "X/USDT", "1h", cfg)
    lim = cut - cfg.max_hold_bars - 1
    ka = {(e.name, e.side, e.i, round(e.r, 9), tuple(sorted(e.ctx.items()))) for e in a if e.i < lim}
    kb = {(e.name, e.side, e.i, round(e.r, 9), tuple(sorted(e.ctx.items()))) for e in b if e.i < lim}
    assert ka and ka == kb


def test_extra_signal_definitions():
    rows = [(100, 101, 99, 100)] * 10 + [(100, 100.5, 96, 96.5), (97, 98.5, 96.8, 98.2), (98.3, 101.5, 98, 101.2)]
    df = pd.DataFrame([{"timestamp": T0 + i * STEP, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                       for i, (o, h, lo, c) in enumerate(rows)])
    atr = np.full(len(df), 2.0)
    names = {(e.name, e.side, e.i) for e in L.extra_events(df, "X/USDT", "1h", atr)}
    assert ("THREE_INSIDE_UP", L.LONG, 12) in names, names
    ev = next(e for e in L.extra_events(df, "X/USDT", "1h", atr) if e.name == "THREE_INSIDE_UP")
    assert abs(ev.stop - (96 - 0.5)) < 1e-9 and ev.t_ms == T0 + 13 * STEP


class FakeProvider:
    def __init__(self, df):
        self.df, self.calls = df, 0

    def klines(self, symbol, tf, limit=1500, start_ms=None, end_ms=None):
        self.calls += 1
        d = self.df[(self.df["timestamp"] >= start_ms) & (self.df["timestamp"] <= end_ms)].head(limit).copy()
        d["is_closed"] = True
        return d


def test_end_to_end_run_writes_report_and_placebo_baseline(tmp_path):
    df = synth(1400, seed=5)
    prov = FakeProvider(df)
    now = int(df["timestamp"].iloc[-1]) + 2 * STEP
    rep = L.run(symbols=["AAA/USDT", "BBB/USDT"], tfs=["1h"], cache_dir=tmp_path / "c", out_dir=tmp_path / "o",
                cfg=L.LabConfig(min_is=5, min_oos=5), provider_factory=lambda: prov, days={"1h": 55}, jobs=1,
                catalog=False, now_ms=now, log=lambda m: None)
    assert prov.calls >= 2 and (tmp_path / "c" / "AAAUSDT_1h.csv.gz").exists()
    doc = json.loads((tmp_path / "o" / "signal_lab_report.json").read_text(encoding="utf-8"))
    assert doc["events"] > 0 and doc["cost_round_trip_pct"] == 0.16
    assert any(g["family"] == "placebo" for g in doc["groups"]) and "placebo" in doc["candidate_rate"]
    assert {g["context"] for g in doc["groups"]} >= {"HEPSİ", "hacim", "rsi", "trend", "volatilite"}
    assert doc["tf_summary"]["1h"]["trades"] > 0 and doc["tf_summary"]["1h"]["cost_r"] > 0
    txt = L.render(rep)
    assert "RASTGELE" in txt and "SİNYAL LABORATUVARI" in txt
    calls = prov.calls                                          # ikinci çalıştırma önbellekten (indirme yok)
    L.run(symbols=["AAA/USDT"], tfs=["1h"], cache_dir=tmp_path / "c", out_dir=tmp_path / "o2", cfg=L.LabConfig(),
          provider_factory=lambda: prov, days={"1h": 55}, jobs=1, catalog=False, now_ms=now, log=lambda m: None)
    assert prov.calls == calls


def test_random_walk_yields_no_strong_candidates():
    """Yöntem kalibrasyonu: gerçek avantajı OLMAYAN (rastgele yürüyüş) veride GÜÇLÜ ADAY çıkmamalı."""
    evs = []
    for k, seed in enumerate((11, 12, 13)):
        e, _ = L.process_series(synth(1800, seed=seed), f"R{k}/USDT", "1h", L.LabConfig())
        evs += [dataclasses.asdict(x) for x in e]
    agg = L.aggregate(evs, L.LabConfig())
    assert agg["tested"] > 50
    assert not [g for g in agg["groups"] if g["verdict"] == L.V_STRONG and g["family"] != "placebo"]


def _events(name: str, family: str, mean: float, n: int, seed: int, side: str = L.LONG) -> list[dict]:
    rnd = np.random.default_rng(seed)
    return [{"symbol": f"S{k % 3}", "tf": "4h", "family": family, "name": name, "side": side, "t_ms": T0 + k * STEP,
             "r": float(x), "cost_r": 0.05, "ctx": {"hacim": "normal"}} for k, x in enumerate(rnd.normal(mean, 1.0, n))]


def test_strong_candidate_must_beat_random_entries_in_the_same_context():
    """"Yükselen piyasada long" tuzağı: sinyal iki dönemde de kazandırsa bile aynı bağlamdaki rastgele long kadar
    kazandırıyorsa GÜÇLÜ ADAY değildir."""
    cfg = L.LabConfig()
    sig = _events("SIG", "extra", 0.5, 600, 1)
    same = L.aggregate(sig + _events("PLACEBO_RANDOM", "placebo", 0.6, 600, 2), cfg)
    lower = L.aggregate(sig + _events("PLACEBO_RANDOM", "placebo", -0.1, 600, 2), cfg)
    pick = lambda agg: next(g for g in agg["groups"] if g["name"] == "SIG" and g["context"] == "HEPSİ")  # noqa: E731
    assert pick(same)["replicated"] and pick(same)["verdict"] == L.V_WEAK
    assert pick(lower)["verdict"] == L.V_STRONG and pick(lower)["vs_placebo"]["OOS"] > 0
    alone = L.aggregate(sig, cfg)                                   # rastgele karşılaştırma yoksa güçlü DENMEZ
    assert pick(alone)["verdict"] == L.V_WEAK and pick(alone)["vs_placebo"] is None


def test_unsupported_timeframe_is_rejected_up_front(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="30m"):
        L.run(symbols=["BTC/USDT"], tfs=["30m"], cache_dir=tmp_path, out_dir=tmp_path / "o", cfg=L.LabConfig(),
              provider_factory=None, log=lambda m: None)


def test_day_clustered_interval_is_not_narrowed_by_coins_moving_together():
    """12 coin aynı gün aynı yönde: işlem düzeyinde bootstrap aralığı 12 kat fazla gözlem sanır; gün kümeli aralık geniş kalır."""
    rnd = np.random.default_rng(3)
    day_r = rnd.normal(0.1, 1.0, 60)
    rs = np.repeat(day_r, 12) + rnd.normal(0, 0.05, 720)
    days = np.repeat(np.arange(60), 12)
    naive, clustered = L.r_stats(rs, 1000), L.r_stats(rs, 1000, days=days)
    width = lambda st: st["ci95"][1] - st["ci95"][0]  # noqa: E731
    assert clustered["days"] == 60 and width(clustered) > 2.5 * width(naive)


def test_listing_after_requested_start_is_not_downloaded_again(tmp_path):
    df = synth(300, seed=9)
    prov = FakeProvider(df)
    now = int(df["timestamp"].iloc[-1]) + 2 * STEP
    kw = dict(days=60, cache_dir=tmp_path, provider_factory=lambda: prov, now_ms=now)   # istenen başlangıç listelemeden önce
    assert len(L.load_series("NEW/USDT", "1h", **kw)) == 300
    calls = prov.calls
    assert len(L.load_series("NEW/USDT", "1h", **kw)) == 300 and prov.calls == calls
