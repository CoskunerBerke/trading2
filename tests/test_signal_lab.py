# -*- coding: utf-8 -*-
"""Sinyal laboratuvarı: gelecek bilgisi yok, adım adımla aynı olaylar, maliyetli ve kötümser işlem simülasyonu."""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

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
    # işlem kesilmiş veride tamamlandıysa (çıkışı kesimden önce), uzun veride AYNI olmalı; tamamlanmadıysa sayılmaz
    lim = cut - cfg.max_hold_bars - 1
    ok = lambda e: (e.i + 1 + e.hold <= cut and e.i < cut - 2) if e.exit else e.i < lim  # noqa: E731
    ka = {(e.name, e.side, e.i, round(e.r, 9), tuple(sorted(e.ctx.items()))) for e in a if ok(e)}
    kb = {(e.name, e.side, e.i, round(e.r, 9), tuple(sorted(e.ctx.items()))) for e in b if ok(e)}
    assert ka and ka == kb
    assert any(e.family == "algo" for e in a), "algoritmalar da gelecekten bağımsız"


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


class DeadProvider:
    def __init__(self):
        self.calls = 0

    def klines(self, *a, **k):
        self.calls += 1
        raise ConnectionResetError(10054, "bağlantı kapatıldı")


def test_download_failures_abort_instead_of_testing_stale_partial_data(tmp_path):
    import pytest
    dead = DeadProvider()
    with pytest.raises(L.DownloadAborted, match="ÇALIŞTIRILMADI"):
        L.run(symbols=["A/USDT", "B/USDT", "C/USDT", "D/USDT"], tfs=["1h"], cache_dir=tmp_path, out_dir=tmp_path / "o",
              cfg=L.LabConfig(), provider_factory=lambda: dead, days={"1h": 30}, log=lambda m: None)
    assert dead.calls == 3 and not (tmp_path / "o" / "signal_lab_report.json").exists()


def test_short_cached_history_is_reported_not_silently_used(tmp_path):
    df = synth(700, seed=4)
    prov = FakeProvider(df)
    now = int(df["timestamp"].iloc[-1]) + 2 * STEP
    L.load_series("OLD/USDT", "1h", days=25, cache_dir=tmp_path / "c", provider_factory=lambda: prov, now_ms=now)
    rep = L.run(symbols=["OLD/USDT"], tfs=["1h"], cache_dir=tmp_path / "c", out_dir=tmp_path / "o", cfg=L.LabConfig(),
                provider_factory=None, days={"1h": 60}, catalog=False, now_ms=now, log=lambda m: None)
    assert rep["data_warnings"] and "EKSİK_GEÇMİŞ" in rep["data_warnings"][0] and "UYARI" in L.render(rep)


def _zip_csv(rows, header=False, micros=False):
    import io
    import zipfile
    buf = io.StringIO()
    if header:
        buf.write("open_time,open,high,low,close,volume,close_time,quote_volume,count,tbv,tbq,ignore\n")
    k = 1000 if micros else 1
    for r in rows:
        buf.write(f"{int(r['timestamp']) * k},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']},"
                  f"{int(r['close_time']) * k},0,0,0,0,0\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", buf.getvalue())
    return out.getvalue()


def test_archive_provider_reads_monthly_and_daily_files():
    """data.binance.vision: biten ay → ay dosyası (eski biçim başlıksız), içinde bulunulan ay → gün dosyaları (başlıklı,
    mikro saniye); olmayan dosya (listeleme öncesi) boş; download() sayfalama REST ile aynı."""
    step = 3_600_000
    t_aug = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp() * 1000)
    t_sep = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp() * 1000)
    now = t_sep + 3 * 86_400_000 + 5 * step                       # 4 Eylül 05:00 — 1-3 Eylül bitmiş günler
    bars = lambda a, n: [{"timestamp": a + i * step, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10,  # noqa: E731
                          "close_time": a + (i + 1) * step - 1} for i in range(n)]
    files = {"monthly/klines/XUSDT/1h/XUSDT-1h-2026-08.zip": _zip_csv(bars(t_aug, 31 * 24))}
    for d in range(3):
        day = t_sep + d * 86_400_000
        files[f"daily/klines/XUSDT/1h/XUSDT-1h-{pd.Timestamp(day, unit='ms', tz='UTC'):%Y-%m-%d}.zip"] = \
            _zip_csv(bars(day, 24), header=True, micros=True)
    asked = []

    def fetch(url):
        asked.append(url)
        return files.get(url.split("/futures/um/")[1])
    prov = L.ArchiveProvider(fetch=fetch, clock_ms=lambda: now)
    df = L.download(prov, "X/USDT", "1h", t_aug - 60 * 86_400_000, now, page=500)
    assert len(df) == 31 * 24 + 3 * 24 and df["timestamp"].is_monotonic_increasing
    assert int(df["timestamp"].iloc[-1]) == t_sep + 3 * 86_400_000 - step, "yalnız bitmiş günler"
    assert int(df["timestamp"].iloc[0]) == t_aug and df["timestamp"].diff().dropna().eq(step).all()
    assert not any("2026-09-04" in u for u in asked), "bugünün dosyası istenmez"


def test_rule_exits_follow_their_own_rule_and_pay_costs():
    cfg = L.LabConfig()
    n = 40
    arr = {k: np.full(n, 100.0) for k in ("open", "high", "low", "close")}
    atr = np.full(n, 2.0)
    aux = {k: np.full(n, np.nan) for k in ("lo10", "hi10", "mom28", "sma5", "sma20")}
    aux["lo10"][:] = 99.0
    arr["close"][15] = 98.5                                   # 15. kapanış 10 bar dibinin altında → 16 açılışta çık
    arr["open"][16] = 98.0
    ev = L.Event("X", "1d", "algo", "TREND_DONCHIAN_20_10", L.LONG, 10, 0, stop=96.0, exit={"kind": "channel", "max_bars": 300})
    assert L.simulate_rule(ev, arr, atr, aux, cfg) == "" and ev.exit_reason == "RULE" and ev.hold == 6
    assert abs(ev.r - ((98.0 - 100.0) - (100.0 + 98.0) * cfg.cost_per_side) / 4.0) < 1e-9
    ev = L.Event("X", "1d", "algo", "XSMOM_28_WEEKLY", L.SHORT, 10, 0, stop=106.0, exit={"kind": "hold", "bars": 7})
    assert L.simulate_rule(ev, arr, atr, aux, cfg) == "" and ev.hold == 8 and ev.exit_reason == "RULE"
    ev = L.Event("X", "1d", "algo", "TREND_DONCHIAN_20_10", L.LONG, 36, 0, stop=96.0, exit={"kind": "channel", "max_bars": 300})
    aux["lo10"][:] = 50.0
    assert L.simulate_rule(ev, arr, atr, aux, cfg) == "NO_FUTURE_DATA", "veri bitmeden kapanmayan işlem sayılmaz"


def test_cross_sectional_momentum_ranks_coins_by_past_return():
    frames = {}
    for k in range(12):
        df = synth(400, seed=100 + k)
        drift = np.exp(np.arange(400) * (0.004 if k == 0 else 0.0))
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * drift
        df["timestamp"] = (df["timestamp"] - STEP * 0) // STEP * 86_400_000
        frames[f"C{k}/USDT"] = df
    evs = L.xsmom_events(frames, L.LabConfig())
    real = [e for e in evs if e.name == L.XSMOM]
    assert real and all(e.exit == {"kind": "hold", "bars": 7} for e in real)
    longs = [e.symbol for e in real if e.side == L.LONG]
    assert longs.count("C0/USDT") >= 0.8 * len({e.t_ms for e in real if e.side == L.LONG}), "sürekli yükselen coin hep güçlüler arasında"
    assert any(e.name == "PLACEBO_" + L.XSMOM for e in evs)


# ================================================================== mum varyasyonları (candle_lab)
H4 = 14_400_000
LOOSE_ID = "CV950_LAB_LOOSE"


def synth4h(n: int, seed: int = 1) -> pd.DataFrame:
    """`synth` ile aynı fiyat yolu, 4h adımlı zaman damgaları."""
    df = synth(n, seed=seed)
    df["timestamp"] = T0 // H4 * H4 + np.arange(n, dtype=np.int64) * H4
    df["close_time"] = df["timestamp"] + H4 - 1
    return df


def _loose_entry(vid: str = LOOSE_ID, **definition) -> dict:
    """Test varyasyonu: tek boğa mum, gövde ≥ %50 (gevşek — rastgele veride sık eşleşir), varsayılan çıkışlar."""
    d = {"side": "LONG", "timeframe": "4h", "bars": [{"color": "bull", "body_range": [0.5, None]}],
         "confirm": {"kind": "pattern_close"}, "stop": {"anchor": "pattern", "atr_buffer": 0.25},
         "exit": {"target_r": 2.0, "max_hold_bars": 24}}
    d.update(definition)
    return {"id": vid, "definition": d}


@pytest.fixture
def registry(monkeypatch):
    """Kaydı bu test için SABİTLER: örnek + verilen varyasyonlar (gerçek kayda kullanıcı varyasyonu eklense de sayılar ve
    `all` listesi değişmez; gerçek kayıt değişmez)."""
    from tradingbot import candle_variations as V

    def add(*entries):
        monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3,) + tuple(entries))
        V.reset_cache()
    yield add
    V.reset_cache()


def _cli():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("_signal_lab_cli_probe", Path(__file__).resolve().parents[1] / "scripts" / "signal_lab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_path_never_touches_candle_lab(monkeypatch, tmp_path):
    from tradingbot import candle_lab

    def boom(*a, **k):
        raise AssertionError("varsayılan laboratuvar yolu candle_lab'a girmemeli")
    for fn in ("variation_events", "vcfg", "build_record", "write_record"):
        monkeypatch.setattr(candle_lab, fn, boom)
    df = synth(700, seed=3)
    evs, meta = L.process_series(df, "X/USDT", "1h", L.LabConfig(), catalog=False)
    assert evs and "variations" not in meta and not any(e.family == "candle_var" for e in evs)
    assert any(e.name == "PLACEBO_RANDOM" for e in evs) and any(e.family == "algo" for e in evs)
    evs2, meta2 = L.process_series(df, "X/USDT", "1h", L.LabConfig(), catalog=False, algos=True, extras=True, variations=())
    assert [dataclasses.asdict(e) for e in evs2] == [dataclasses.asdict(e) for e in evs] and meta2 == meta
    prov = FakeProvider(synth(900, seed=5))
    now = int(prov.df["timestamp"].iloc[-1]) + 2 * STEP
    rep = L.run(symbols=["AAA/USDT"], tfs=["1h"], cache_dir=tmp_path / "c", out_dir=tmp_path / "o", cfg=L.LabConfig(),
                provider_factory=lambda: prov, days={"1h": 37}, jobs=1, catalog=False, now_ms=now, log=lambda m: None)
    assert rep["events"] > 0 and "variations" not in rep and "trials_to_date" not in rep
    assert not (tmp_path / "o" / "variation_records").exists()
    assert "MUM VARYASYONLARI" not in L.render(rep)
    # eski 8'li görev demeti hâlâ çalışır (geri uyumlu)
    evs3, meta3 = L._task((str(tmp_path / "c"), "AAA/USDT", "1h", 37, now, dataclasses.asdict(L.LabConfig()), False, True))
    assert evs3 and "variations" not in meta3


def test_variation_events_simulated_with_own_exit_spec(registry):
    from tradingbot import candle_lab as CL
    from tradingbot import candle_variations as V
    vid = "CV951_LAB_EXIT"
    registry(_loose_entry(vid, exit={"target_r": 1.5, "max_hold_bars": 6}, risk_atr_bounds=[0.3, 1.2]))
    var = V.get(vid)
    vc = CL.vcfg(L.LabConfig(), var)
    assert (vc.default_rr, vc.max_hold_bars, vc.min_risk_atr, vc.max_risk_atr) == (1.5, 6, 0.3, 1.2)
    assert dataclasses.replace(vc, default_rr=2.0, max_hold_bars=24, min_risk_atr=0.1, max_risk_atr=5.0) == L.LabConfig()
    no_target = CL.vcfg(L.LabConfig(), dataclasses.replace(var, target_r=None))
    assert no_target.default_rr == float("inf"), "hedefsiz varyasyon: yalnız stop ve zaman çıkışı"
    df = synth4h(1100, seed=7)
    evs, meta = L.process_series(df, "X/USDT", "4h", L.LabConfig(), catalog=False, algos=False, extras=False, variations=(vid,))
    raw, atr_sig = CL.variation_events(df, "X/USDT", "4h", var)
    real = [e for e in evs if e.family == "candle_var"]
    assert len(real) >= 20
    o = df["open"].to_numpy(dtype=float)
    for e in real:
        risk = o[e.i + 1] - e.stop                              # giriş sonraki barın açılışı
        assert 0.3 * atr_sig[e.i] < risk <= 1.2 * atr_sig[e.i], "risk sınırları tanımdan (pencere ATR14'ü ile)"
        assert 1 <= e.hold <= 6
        gross = e.r + e.cost_r
        if e.exit_reason == "TIME":
            assert e.hold == 6
        elif e.exit_reason == "TARGET":
            assert gross >= 1.5 - 1e-9
        else:
            assert e.exit_reason == "STOP" and gross <= -1.0 + 1e-9
    assert any(e.exit_reason == "TARGET" and abs(e.r + e.cost_r - 1.5) < 1e-9 for e in real)
    assert any(e.exit_reason == "TIME" for e in real)
    sk = meta["skipped"]
    assert sk.get(f"{vid}:STOP_TOO_FAR", 0) > 0 and all(k.startswith((vid + ":", "PLACEBO_" + vid + ":")) for k in sk)
    m = meta["variations"][vid]
    assert m["signals"] == sum(e.family == "candle_var" for e in raw) and m["trades"] == len(real)
    assert m["placebo_signals"] == sum(e.family == "placebo" for e in raw) and m["placebo_trades"] == len(evs) - len(real)
    assert meta["signals"] == len(raw) and meta["trades"] == len(evs)
    # aynı olaylar LabConfig varsayılan çıkışıyla başka sonuç verir (çıkış gerçekten tanımdan)
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    wide = [dataclasses.replace(e) for e in raw if e.family == "candle_var"]
    kept = sum(1 for e in wide if not L.simulate(e, arr, atr_sig, L.LabConfig()))
    assert kept > len(real)


def test_only_variations_skips_catalog_extras_algos(registry, monkeypatch, tmp_path, capsys):
    from tradingbot import candle_variations as V
    registry(_loose_entry())
    df = synth4h(800, seed=4)
    evs, meta = L.process_series(df, "X/USDT", "4h", L.LabConfig(), catalog=False, algos=False, extras=False, variations=(LOOSE_ID,))
    assert {e.family for e in evs} == {"candle_var", "placebo"} and {e.name for e in evs} == {LOOSE_ID, "PLACEBO_" + LOOSE_ID}
    both, _ = L.process_series(df, "X/USDT", "4h", L.LabConfig(), catalog=False, variations=(LOOSE_ID,))
    assert {"extra", "algo"} <= {e.family for e in both} and any(e.name == "PLACEBO_RANDOM" for e in both)
    cli = _cli()
    seen: dict = {}

    def fake_run(**kw):
        seen.clear()
        seen.update(kw)
        return {"groups": [], "symbols": kw["symbols"], "timeframes": kw["tfs"]}
    monkeypatch.setattr(L, "run", fake_run)
    out = str(tmp_path / "o")
    assert cli.main(["--only-variations", "--variations", LOOSE_ID.lower(), "--offline", "--tfs", "4h", "--out", out]) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["variations"]) == (False, False, False, [LOOSE_ID])
    assert "readback" in capsys.readouterr().out, "çeviri onayı olmayan kayıt için uyarı"
    assert cli.main(["--offline", "--tfs", "4h", "--out", out]) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["variations"]) == (True, True, True, [])
    assert cli.main(["--only-variations", "--variations", "all", "--offline", "--out", out]) == 0
    assert seen["variations"] == [e["id"] for e in V.VARIATIONS if not e.get("example")] == [LOOSE_ID], "all = örnek olmayanlar"
    assert (seen["catalog"], seen["algos"], seen["extras"]) == (False, False, False)
    seen.clear()
    assert cli.main(["--only-variations", "--offline", "--out", out]) == 2 and not seen
    # katalogla birlikte varyasyon koşusu reddedilir (keşif/doğrulama kesimi değişir; kapı böyle kaydı almaz)
    assert cli.main(["--variations", LOOSE_ID, "--no-catalog", "--offline", "--out", out]) == 2 and not seen
    assert "--only-variations" in capsys.readouterr().err
    assert cli.main(["--variations", "CV999_NOPE", "--offline", "--out", out]) == 2 and not seen
    assert "UNKNOWN_ID" in capsys.readouterr().err


def test_variation_record_written(registry, monkeypatch, tmp_path):
    import re

    from tradingbot import candle_dsl as D
    from tradingbot import candle_lab as CL
    from tradingbot import candle_variations as V
    registry(dict(_loose_entry(), readback={"confirmed_by": "user", "date": "2000-01-01"}))
    var = V.get(LOOSE_ID)
    df = synth4h(900, seed=5)
    prov = FakeProvider(df)
    now = int(df["timestamp"].iloc[-1]) + 2 * H4
    for k in ("GITHUB_RUN_ID", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_SERVER_URL"):
        monkeypatch.delenv(k, raising=False)
    kw = dict(symbols=["AAA/USDT", "BBB/USDT"], tfs=["4h"], cache_dir=tmp_path / "c", cfg=L.LabConfig(min_is=5, min_oos=5),
              provider_factory=lambda: prov, days={"4h": 150}, jobs=1, catalog=False, algos=False, extras=False, now_ms=now,
              log=lambda m: None, variations=(LOOSE_ID,))
    rep = L.run(out_dir=tmp_path / "local", **kw)
    path = tmp_path / "local" / "variation_records" / f"{LOOSE_ID}.json"

    def strict(s):
        raise ValueError(f"JSON'da sonlu olmayan sayı: {s}")
    rec = json.loads(path.read_text(encoding="utf-8"), parse_constant=strict)
    assert rec["record_schema"] == "candle_lab/1" and rec["id"] == LOOSE_ID and rec["definition_sha"] == var.definition_sha
    assert rec["dsl_version"] == D.DSL_VERSION and rec["window"] == D.WINDOW == 500
    assert rec["golden_sha"] == CL.golden_sha(var) and re.fullmatch(r"[0-9a-f]{16}", rec["golden_sha"])
    assert rec["run"]["github_run_id"] is None and rec["run"]["run_url"] is None and rec["run"]["commit"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rec["run"]["completed_at"])
    assert rec["universe"] == {"symbols": ["AAA/USDT", "BBB/USDT"], "tfs": ["4h"], "days": {"4h": 150}}
    b = rec["by_tf"]["4h"]
    assert rec["primary_tf"] == "4h" and rec["verdict"] == b["verdict"] and rec["cutoff_ms"]["4h"] == rep["cutoff_ms"]["4h"]
    assert b["trades"] > 10 and b["IS"]["n"] + b["OOS"]["n"] == b["trades"] and b["signals"] >= b["trades"] and b["symbols"] == 2
    assert b["vs_placebo"] is not None and b["placebo_trades"] > 0
    no = b["non_overlap_mean_r"]
    assert 0 < no["n"]["IS"] <= b["IS"]["n"] and 0 < no["n"]["OOS"] <= b["OOS"]["n"] and no["IS"] is not None
    assert rec["trials_to_date"] == V.trials_to_date() == 1 and rec["definition"] == var.definition and rec["side"] == "LONG"
    # koşu anındaki çeviri onayı ve koşunun kipi kayda girer (kapı ikisini de denetler); bağımlılık sürümleri teşhis için
    assert rec["readback"] == var.readback == {"confirmed_by": "user", "date": "2000-01-01"}
    assert rec["run"]["mode"] == {"catalog": False, "algos": False, "extras": False, "only_variations": True}
    assert set(rec["run"]["versions"]) == {"python", "numpy", "pandas"}
    assert rep["variations"][LOOSE_ID]["definition_sha"] == var.definition_sha and rep["trials_to_date"] == 1
    txt = L.render(rep)
    assert "MUM VARYASYONLARI" in txt and var.definition_sha in txt and LOOSE_ID in txt
    # yerel çalıştırmanın kaydı kapıdan geçmez
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", path.parent)
    assert V.gate(LOOSE_ID) == (None, "LAB_NOT_FROM_CI")
    # GitHub Actions ortamı → çalıştırma bilgisi kayda yazılır; kapı kaynak denetimlerini geçer, onay aşamasına gelir
    for k, v in (("GITHUB_RUN_ID", "4242"), ("GITHUB_REPOSITORY", "owner/repo"), ("GITHUB_SHA", "abc123"),
                 ("GITHUB_SERVER_URL", "https://github.com")):
        monkeypatch.setenv(k, v)
    L.run(out_dir=tmp_path / "ci", **kw)
    ci_path = tmp_path / "ci" / "variation_records" / f"{LOOSE_ID}.json"
    ci = json.loads(ci_path.read_text(encoding="utf-8"))
    assert ci["run"]["github_run_id"] == "4242" and ci["run"]["commit"] == "abc123"
    assert ci["run"]["run_url"] == "https://github.com/owner/repo/actions/runs/4242"
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", ci_path.parent)
    assert V.gate(LOOSE_ID)[1] in ("NO_APPROVAL", "VERDICT_LOSS")


def test_candle_variation_is_compared_only_with_its_matched_placebo():
    cfg = L.LabConfig()
    pick = lambda agg, name: next(g for g in agg["groups"] if g["name"] == name and g["context"] == "HEPSİ")  # noqa: E731
    sig = _events("CV001_X", "candle_var", 0.5, 600, 1)
    rnd = _events("PLACEBO_RANDOM", "placebo", -0.5, 600, 2)
    own = _events("PLACEBO_CV001_X", "placebo", 0.4, 600, 3)
    alone = pick(L.aggregate(sig + rnd, cfg), "CV001_X")
    assert alone["vs_placebo"] is None and alone["verdict"] == L.V_WEAK, "PLACEBO_RANDOM'a düşmez"
    g = pick(L.aggregate(sig + rnd + own, cfg), "CV001_X")
    assert g["vs_placebo"]["placebo_mean_r"] == [pick(L.aggregate(own, cfg), "PLACEBO_CV001_X")[p]["mean_r"] for p in ("IS", "OOS")]
    # diğer aileler değişmedi: formasyon hâlâ genel rastgele girişle karşılaştırılır
    assert pick(L.aggregate(_events("SIG", "extra", 0.5, 600, 1) + rnd, cfg), "SIG")["vs_placebo"] is not None


def test_random_walk_loose_variation_is_not_strong(registry):
    """Yöntem kalibrasyonu: avantajı OLMAYAN veride gevşek bir mum varyasyonu GÜÇLÜ ADAY çıkmamalı; eşi (aynı bağlam, aynı
    stop ve çıkış) karşılaştırmada bulunur."""
    registry(_loose_entry())
    evs = []
    for k, seed in enumerate((11, 12, 13)):
        e, _ = L.process_series(synth4h(1800, seed=seed), f"R{k}/USDT", "4h", L.LabConfig(), catalog=False, algos=False,
                                extras=False, variations=(LOOSE_ID,))
        evs += [dataclasses.asdict(x) for x in e]
    agg = L.aggregate(evs, L.LabConfig())
    mine = [g for g in agg["groups"] if g["family"] == "candle_var"]
    hepsi = next(g for g in mine if g["context"] == "HEPSİ")
    assert hepsi["IS"]["n"] >= 30 and hepsi["OOS"]["n"] >= 20 and hepsi["vs_placebo"] is not None
    assert not [g for g in mine if g["verdict"] == L.V_STRONG]
