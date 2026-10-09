# -*- coding: utf-8 -*-
"""Vadeli laboratuvar (SPEC fut_v1 §5–§7, §10 test 11–19): kurallar, kontroller (tam tümleyen), plasebolar, fonlama
taşıma, katkı ve defter adaylığı, rastgele yürüyüş kalibrasyonu, kayıt sha'sı, komut satırı. Ağ YOK: sahte `fetch`."""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import importlib.util
import io
import json
import threading
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import futures_data as FD
from tradingbot import futures_features as FF
from tradingbot import futures_lab as FL
from tradingbot import signal_lab as L

DAY = 86_400_000
HOUR = 3_600_000
H4 = 4 * HOUR
T0 = 1_700_006_400_000                                         # gün başı (UTC)
ROOT = Path(__file__).resolve().parents[1]

#: ön kayıt mührü — bir sabit ya da kural metni değişirse bu test KIRILIR (fut_v2 + belge + yeni deneme sayısı)
PINNED_SHA = "6f08eca51d8b97ee"


# ---------------------------------------------------------------------------- yardımcılar
def neutral(n: int = 300, step: int = H4):
    """Hiçbir kuralın tetiklenmediği düz seri + elle kurulan gösterge/yardımcı/özellik dizileri (birim testleri)."""
    ts = T0 + np.arange(n, dtype=np.int64) * step
    df = pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0})
    ind = {"atr": np.full(n, 1.0), "ema50": np.full(n, 99.0), "ema200": np.full(n, 98.0), "vol_avg": np.full(n, 50.0)}
    aux = {"hi20": np.full(n, 101.0), "lo20": np.full(n, 99.0)}
    feat = {k: np.zeros(n) for k in ("doi", "oi_z", "px_z", "oi_pct", "f8", "f8_hi", "f8_lo", "dpx", "oi")}
    feat["OI_OK"], feat["FUND_OK"] = np.ones(n, dtype=bool), np.ones(n, dtype=bool)
    return df, ind, aux, feat


def real(evs, *names):
    return [(e.family, e.name, e.side, e.i) for e in evs if e.family != "placebo" and (not names or e.name in names)]


def setbar(df, i, **kw):
    for k, v in kw.items():
        df.loc[i, k] = v


# ---------------------------------------------------------------------------- 11. #1 OI_BREAKOUT_20
def test_oi_breakout_rule_control_and_every_price_condition():
    def run(doi=0.1, ok=True, **mod):
        df, ind, aux, feat = neutral()
        setbar(df, 240, close=102.0, high=102.0, volume=100.0)
        feat["doi"][240], feat["OI_OK"][240] = doi, ok
        for key, (i, v) in mod.items():
            if key in df:
                df.loc[i, key] = v
            else:
                (ind[key] if key in ind else aux[key])[i] = v
        return real(FL.events(df, "X/USDT", "4h", ind, aux, feat), "OI_BREAKOUT_20", "CTRL_OI_BREAKOUT_20")
    assert run() == [("futures", "OI_BREAKOUT_20", L.LONG, 240)], "OI yükseliyor → kural"
    assert run(doi=-0.1) == [("control", "CTRL_OI_BREAKOUT_20", L.LONG, 240)], "OI düşüyor → kontrol"
    assert run(doi=0.0) == [("control", "CTRL_OI_BREAKOUT_20", L.LONG, 240)], "doi ≤ 0 kontrol kenarı"
    assert run(ok=False) == [] and run(doi=float("nan"), ok=False) == [], "OI bilinmiyor → ne kural ne kontrol"
    # fiyat koşullarının HER biri gerekli
    assert [x for x in run(close=(239, 101.5)) if x[3] == 240] == [], "önceki kapanış kanal üstünde (ilk kırılım değil)"
    assert run(ema50=(240, 97.0)) == [], "c > ema50 > ema200 değil"
    assert run(volume=(240, 75.0)) == [], "hacim tam 1.5x (kesin büyük gerekir)"
    assert run(vol_avg=(240, 0.0)) == [], "vol_avg > 0 gerekir"
    assert run(hi20=(240, 102.0)) == [], "kapanış kanal üstünde değil"
    assert run(atr=(240, float("nan"))) == [] and run(atr=(240, 0.0)) == [], "ATR geçersiz"
    df, ind, aux, feat = neutral()
    setbar(df, 240, close=98.0, low=98.0, volume=100.0)
    ind["ema50"][:], ind["ema200"][:] = 101.0, 102.0
    feat["doi"][240] = 0.2
    ev = [e for e in FL.events(df, "X/USDT", "4h", ind, aux, feat) if e.family == "futures"]
    assert [(e.name, e.side, e.i) for e in ev] == [("OI_BREAKOUT_20", L.SHORT, 240)], "ayna: yeni short'lar"
    assert ev[0].stop == 98.0 + 2.0 and ev[0].exit == {"kind": "channel", "max_bars": 300} and ev[0].t_ms == T0 + 241 * H4


# ---------------------------------------------------------------------------- 12. #9 rejim
def test_regime_cross_bars_refractory_and_exact_complement():
    df, ind, aux, feat = neutral()
    pz, oz = feat["px_z"], feat["oi_z"]
    pz[230], oz[230] = 1.2, 1.5                  # yukarı kesişim, yeni pozisyon
    pz[231], pz[232], pz[233] = 0.5, 0.8, 1.3    # 233: aynı yönde tekrar kesişim, k = 6 bar içinde → yok sayılır
    pz[240], oz[240] = 1.1, -1.5                 # 240 − 230 = 10 > 6 → kabul; tasfiye
    pz[241:250] = 1.1                            # üstte kalmak kesişim değildir
    pz[250], oz[250] = -1.2, 0.3                 # aşağı kesişim
    pz[251:260] = -1.3
    evs = FL.events(df, "X/USDT", "4h", ind, aux, feat)
    cont = set((e.symbol, e.i, e.side) for e in evs if e.name in ("OI_REGIME_CONT", "CTRL_OI_REGIME_CONT"))
    rev = set((e.symbol, e.i, e.side) for e in evs if e.name in ("OI_REGIME_REVERT", "CTRL_OI_REGIME_REVERT"))
    trig = {230: L.LONG, 240: L.LONG, 250: L.SHORT}
    assert cont == {("X/USDT", i, s) for i, s in trig.items()}, "kural ∪ kontrol = tetik kümesi (devam)"
    flip = {L.LONG: L.SHORT, L.SHORT: L.LONG}
    assert rev == {("X/USDT", i, flip[s]) for i, s in trig.items()}, "kural ∪ kontrol = tetik kümesi (dönüş)"
    got = sorted(real(evs, "OI_REGIME_CONT", "CTRL_OI_REGIME_CONT", "OI_REGIME_REVERT", "CTRL_OI_REGIME_REVERT"), key=lambda x: (x[3], x[1]))
    assert got == [("control", "CTRL_OI_REGIME_REVERT", L.SHORT, 230), ("futures", "OI_REGIME_CONT", L.LONG, 230),
                   ("control", "CTRL_OI_REGIME_CONT", L.LONG, 240), ("futures", "OI_REGIME_REVERT", L.SHORT, 240),
                   ("control", "CTRL_OI_REGIME_CONT", L.SHORT, 250), ("control", "CTRL_OI_REGIME_REVERT", L.LONG, 250)]
    e = next(x for x in evs if x.name == "OI_REGIME_CONT")
    assert e.exit == {"kind": "hold", "bars": 6} and e.stop == 100.0 - 2.0
    feat["OI_OK"][240] = False                   # OI bilinmiyor → tetik yok (ve bekleme süresi başlamaz)
    assert 240 not in {x.i for x in FL.events(df, "X/USDT", "4h", ind, aux, feat) if x.family != "placebo"}
    one_d = FL.events(df.assign(timestamp=T0 + np.arange(len(df), dtype=np.int64) * DAY), "X/USDT", "1d", ind, aux, feat)
    assert next(x for x in one_d if x.name == "OI_REGIME_CONT").exit == {"kind": "hold", "bars": 1}, "1d: k = 1"


# ---------------------------------------------------------------------------- 13. #10 süpürme
def test_funding_sweep_wick_crowding_and_control():
    def run(o=100.5, c=100.7, f8=0.0005, hi=0.95, lo=0.0, pct=0.85, fund_ok=True, h=101.9, low=99.9):
        df, ind, aux, feat = neutral()
        setbar(df, 240, open=o, close=c, high=h, low=low)
        feat["f8"][240], feat["f8_hi"][240], feat["f8_lo"][240], feat["oi_pct"][240] = f8, hi, lo, pct
        feat["FUND_OK"][240] = fund_ok
        return [e for e in FL.events(df, "X/USDT", "4h", ind, aux, feat) if e.family != "placebo"]
    ev = run()                                   # üst fitil 1.2 / aralık 2.0 = %60
    assert [(e.family, e.name, e.side, e.i) for e in ev] == [("futures", "FUNDING_SWEEP_REVERSAL", L.SHORT, 240)]
    assert abs(ev[0].stop - (101.9 + 0.25)) < 1e-12 and ev[0].trigger == 100.7 and ev[0].exit == {}
    assert run(o=101.1, c=100.5) == [], "%40 fitil → yok"
    assert [(e.family, e.name) for e in run(f8=0.0001)] == [("control", "CTRL_FUNDING_SWEEP_REVERSAL")], "1 bp taban → kontrol"
    assert [(e.family, e.name) for e in run(hi=0.89)] == [("control", "CTRL_FUNDING_SWEEP_REVERSAL")]
    assert [(e.family, e.name) for e in run(pct=0.79)] == [("control", "CTRL_FUNDING_SWEEP_REVERSAL")]
    assert run(fund_ok=False) == [], "fonlama bilinmiyor → ne kural ne kontrol"
    lg = run(o=100.5, c=100.3, h=100.9, low=98.9, f8=-0.0003, hi=0.0, lo=0.95)   # alt fitil 1.4 / 2.0 = %70
    assert [(e.family, e.name, e.side) for e in lg] == [("futures", "FUNDING_SWEEP_REVERSAL", L.LONG)]
    assert abs(lg[0].stop - (98.9 - 0.25)) < 1e-12
    assert [(e.family, e.side) for e in run(o=100.5, c=100.3, h=100.9, low=98.9, f8=0.0003, hi=0.0, lo=0.95)] == \
        [("control", L.LONG)], "LONG kalabalığı negatif fonlama ister"


# ---------------------------------------------------------------------------- 14. plasebolar
def _placebos(evs, name=None):
    return [(e.name, e.side, e.i, round(e.stop, 10)) for e in evs if e.family == "placebo" and (name is None or e.name == name)]


def test_placebos_deterministic_masked_and_independent_of_real_signals():
    n = 6000
    df, ind, aux, feat = neutral(n)
    feat["OI_OK"][1000:1500] = False
    feat["FUND_OK"][2000:2600] = False
    a = FL.events(df, "X/USDT", "4h", ind, aux, feat)
    assert _placebos(a) == _placebos(FL.events(df, "X/USDT", "4h", ind, aux, feat)), "belirlenimci"
    assert not real(a), "düz seride gerçek olay yok"
    for e in a:
        if e.family == "placebo":
            assert feat["OI_OK"][e.i] and e.i >= FL.START_BAR
            if e.name == "PLACEBO_FUNDING_SWEEP_REVERSAL":
                assert feat["FUND_OK"][e.i]
    # gerçek olaylar eklenince #1/#9 plaseboları DEĞİŞMEZ (seçim yalnız zaman damgası + maske)
    df2, ind2, aux2, feat2 = neutral(n)
    feat2["OI_OK"][1000:1500] = False
    feat2["FUND_OK"][2000:2600] = False
    for i in range(300, n, 97):
        feat2["px_z"][i], feat2["oi_z"][i] = 1.5, 1.5
        setbar(df2, i, close=102.0, high=102.0)
        feat2["doi"][i] = 0.1
    b = FL.events(df2, "X/USDT", "4h", ind2, aux2, feat2)
    assert real(b, "OI_BREAKOUT_20") and real(b, "OI_REGIME_CONT")
    for nm in ("PLACEBO_OI_BREAKOUT_20", "PLACEBO_OI_REGIME_CONT", "PLACEBO_OI_REGIME_REVERT"):
        assert [x[:3] for x in _placebos(a, nm)] == [x[:3] for x in _placebos(b, nm)]
    # çekiliş oranı ve yön payı (toleranslı)
    m = int(feat["OI_OK"][FL.START_BAR:].sum())
    for nm in ("PLACEBO_OI_BREAKOUT_20", "PLACEBO_OI_REGIME_CONT", "PLACEBO_OI_REGIME_REVERT"):
        got = _placebos(a, nm)
        assert abs(len(got) / m - 0.01) < 0.004, (nm, len(got) / m)
        assert 0.25 < sum(1 for x in got if x[1] == L.LONG) / len(got) < 0.75
        assert {e.exit["kind"] for e in a if e.name == nm} == ({"channel"} if "BREAKOUT" in nm else {"hold"})


def test_sweep_placebo_risk_comes_only_from_earlier_parents():
    n = 6000
    df, ind, aux, feat = neutral(n)
    setbar(df, 3000, open=100.5, close=100.7, high=101.9, low=99.9)       # tek SHORT süpürme ebeveyni (kontrol)
    sk: dict = {}
    evs = FL.events(df, "X/USDT", "4h", ind, aux, feat, sk)
    assert real(evs) == [("control", "CTRL_FUNDING_SWEEP_REVERSAL", L.SHORT, 3000)]
    q = (101.9 + 0.25 - 100.7) / 1.0
    pl = [e for e in evs if e.name == "PLACEBO_FUNDING_SWEEP_REVERSAL"]
    assert pl and all(e.side == L.SHORT and e.i > 3000 for e in pl), "ebeveyn yalnız j'den ÖNCE ise"
    assert all(abs(e.stop - (100.0 + q)) < 1e-9 and e.trigger == 100.0 and e.exit == {} for e in pl)
    # beklenen çekilişler elle: u < 0.02, yön karması; ebeveynsiz olanlar NO_REAL_RISK sayılır
    drawn = [(j, L.LONG if FL._h2("X/USDT", "4h", "PLACEBO_FUNDING_SWEEP_REVERSAL", "side", int(df["timestamp"][j])) < 0.5 else L.SHORT)
             for j in range(FL.START_BAR, n) if L._h("X/USDT", "4h", "PLACEBO_FUNDING_SWEEP_REVERSAL", int(df["timestamp"][j])) < 0.02]
    ok = [(j, s) for j, s in drawn if s == L.SHORT and j > 3000]
    assert [(e.i, e.side) for e in pl] == ok
    assert sk == {"PLACEBO_FUNDING_SWEEP_REVERSAL:NO_REAL_RISK": len(drawn) - len(ok)}
    assert abs(len(drawn) / (n - FL.START_BAR) - 0.02) < 0.006
    assert 0.3 < sum(1 for _, s in drawn if s == L.LONG) / len(drawn) < 0.7, "yön çekilişi kabul çekilişinden bağımsız"


# ---------------------------------------------------------------------------- 15. fonlama taşıma
def test_funding_carry_sign_boundaries_and_unknown():
    n = 60
    ts = T0 + np.arange(n, dtype=np.int64) * H4
    arr = {"open": 100.0 + np.arange(n, dtype=float), "close": 100.5 + np.arange(n, dtype=float)}
    f_t = ts.copy()                                                     # her bar açılışında (4 saatte bir) uzlaşma
    rate = 1e-4 * (1 + np.arange(n, dtype=float))                       # her uzlaşma ayırt edilir
    raw = {"f_t": f_t, "f_rate": rate}

    def ev(side, reason, hold=6, i=10, name="OI_BREAKOUT_20"):
        s = 1 if side == L.LONG else -1
        return L.Event("X", "4h", "futures", name, side, i, 0, stop=float(arr["open"][i + 1] - s * 2.0), exit_reason=reason, hold=hold)
    j, e = 11, 16
    a, b = ev(L.LONG, "RULE"), ev(L.LONG, "TIME")
    sh, other = ev(L.SHORT, "STOP"), ev(L.LONG, "RULE", name="TREND_DONCHIAN_20_10")
    FL.funding_carry([a, b, sh, other], arr, ts, H4, raw)
    rule_set = sum(rate[k] * arr["open"][k] for k in range(j + 1, e + 1))   # ts[j] < t ≤ ts[e] (giriş anı hariç)
    assert abs(a.funding_r - (-rule_set / 2.0)) < 1e-12 and a.funding_r < 0, "long pozitif fonlama öder"
    assert abs(b.funding_r - (-(rule_set + rate[e + 1] * arr["open"][e + 1]) / 2.0)) < 1e-12, "TIME: çıkış = kapanış"
    assert abs(sh.funding_r - rule_set / 2.0) < 1e-12 and sh.funding_r > 0, "short alır"
    assert other.funding_r is None, "vadeli olmayan olay dokunulmaz"
    gap = {"f_t": np.delete(f_t, [13, 14]), "f_rate": np.delete(rate, [13, 14])}   # 12 saatlik boşluk → aralık bilinmiyor
    c = ev(L.LONG, "RULE")
    FL.funding_carry([c], arr, ts, H4, gap)
    assert np.isnan(c.funding_r)
    late = ev(L.LONG, "RULE", i=50, hold=6)                             # çıkıştan sonra uzlaşma yok → bilinmiyor
    FL.funding_carry([late], arr, ts, H4, {"f_t": f_t[:57], "f_rate": rate[:57]})
    assert np.isnan(late.funding_r)
    early = ev(L.LONG, "RULE")                                          # girişten önce uzlaşma yok → bilinmiyor
    FL.funding_carry([early], arr, ts, H4, {"f_t": f_t[12:], "f_rate": rate[12:]})
    assert np.isnan(early.funding_r)


# ---------------------------------------------------------------------------- sentetik veri (fiyat + OI + fonlama)
def rw_prices(n: int, seed: int, step: int = H4, t0: int = T0) -> pd.DataFrame:
    """Rastgele yürüyüş mumları (avantaj YOK); hacim log-normal (yüksek hacim barları da olur)."""
    rnd = np.random.default_rng(seed)
    ts = t0 + np.arange(n, dtype=np.int64) * step
    c = 100.0 * np.exp(np.cumsum(rnd.normal(0, 0.012, n)))
    o = np.concatenate([[100.0], c[:-1]])
    wig = np.abs(rnd.normal(0, 0.006, (2, n)))
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * (1 + wig[0]), "low": np.minimum(o, c) * (1 - wig[1]),
                         "close": c, "volume": 100.0 * np.exp(rnd.normal(0, 0.5, n)), "close_time": ts + step - 1})


def rw_raw(t_from: int, t_to: int, seed: int) -> dict:
    """Fiyattan BAĞIMSIZ rastgele OI (5 dk) ve fonlama (8 saat; +0..5 ms oynama)."""
    rnd = np.random.default_rng(seed + 10_000)
    oi_t = np.arange(t_from - t_from % 300_000, t_to, 300_000, dtype=np.int64)
    oi = 1e6 * np.exp(np.cumsum(rnd.normal(0, 0.003, oi_t.size)))
    f_t = np.arange(t_from - t_from % (8 * HOUR), t_to, 8 * HOUR, dtype=np.int64)
    return {"oi_t": oi_t, "oi": oi, "f_t": f_t + rnd.integers(0, 6, f_t.size), "f_rate": rnd.normal(1e-4, 2e-4, f_t.size)}


def _zip(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", text)
    return out.getvalue()


class Archive:
    """data.binance.vision taklidi (yalnız vadeli dosyalar): `raw` satırlarından gün/ay zip'leri üretir; istekler
    kaydedilir; `dead` → her istek ConnectionError."""

    def __init__(self, raws: dict[str, dict], dead: bool = False):
        self.raws, self.dead, self.asked = raws, dead, []
        self.lock = threading.Lock()

    def __call__(self, url: str):
        with self.lock:
            self.asked.append(url)
        if self.dead:
            raise ConnectionError(f"arşiv indirilemedi: {url}")
        name = url.rsplit("/", 1)[-1][:-4]
        sym = name.split("-")[0]
        raw = self.raws.get(sym)
        if raw is None or url.endswith(".CHECKSUM"):
            return None
        if "/daily/metrics/" in url:
            d = int(pd.Timestamp(name[-10:], tz="UTC").timestamp() * 1000)
            m = (raw["oi_t"] >= d) & (raw["oi_t"] < d + DAY)
            if not m.any():
                return None
            tt = pd.to_datetime(raw["oi_t"][m], unit="ms").strftime("%Y-%m-%d %H:%M:%S")
            rows = [f"{t},{sym},{v:.4f},{v * 50:.2f},1,1,1,1" for t, v in zip(tt, raw["oi"][m])]
            return _zip("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
                        "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n" + "\n".join(rows) + "\n")
        if "/monthly/fundingRate/" in url:
            a = int(pd.Timestamp(name[-7:] + "-01", tz="UTC").timestamp() * 1000)
            m = (raw["f_t"] >= a) & (raw["f_t"] < L._next_month(a))
            if not m.any():
                return None
            rows = [f"{t},8,{r:.8f}" for t, r in zip(raw["f_t"][m], raw["f_rate"][m])]
            return _zip("calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(rows) + "\n")
        return None


class Klines:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def klines(self, symbol, tf, limit=1500, start_ms=None, end_ms=None):
        df = self.frames[symbol]
        d = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)].head(limit).copy()
        d["is_closed"] = True
        return d


def lab_world(n: int = 1000, syms=("AAA/USDT", "BBB/USDT")):
    frames = {s: rw_prices(n, 50 + k) for k, s in enumerate(syms)}
    now = int(frames[syms[0]]["timestamp"].iloc[-1]) + 2 * H4
    raws = {s.replace("/", ""): rw_raw(now - 400 * DAY, now, 50 + k) for k, s in enumerate(syms)}
    return frames, raws, now


def _strict_json(path: Path) -> dict:
    def bad(s):
        raise ValueError(f"JSON'da sonlu olmayan sayı: {s}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=bad)


# ---------------------------------------------------------------------------- 8 (olay yarısı). nedensellik ve önek
def test_rule_control_and_placebo_events_are_causal_and_prefix_invariant():
    n, cut = 1500, 1100
    df = rw_prices(n, 3)
    raw = rw_raw(T0 - 40 * DAY, T0 + (n + 2) * H4, 3)
    step = H4

    def evs(d, r):
        feat = FF.bar_features(d["timestamp"].to_numpy(dtype=np.int64), step, d["close"].to_numpy(dtype=float), r)
        return FL.events(d, "X/USDT", "4h", L.indicators(d), L.aux_series(d), feat, {})
    key = lambda es: [(e.family, e.name, e.side, e.i, e.t_ms, round(e.stop, 9), e.trigger, json.dumps(e.exit)) for e in es if e.i <= cut]  # noqa: E731
    full = key(evs(df, raw))
    assert {x[0] for x in full} >= {"futures", "control", "placebo"}, "sentetik veride her aile var"
    t_cut = int(df["timestamp"][cut]) + step
    pert = df.copy()
    pert.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.7
    pert.loc[cut + 1:, "volume"] *= 9.0
    r2 = {k: np.array(v, copy=True) for k, v in raw.items()}
    r2["oi"][r2["oi_t"] > t_cut - FF.OI_LAG_MS] *= 3.0
    r2["f_rate"][r2["f_t"] > t_cut - FF.FUND_GUARD_MS] = 0.01
    assert key(evs(pert, r2)) == full, "gelecek fiyat/OI/fonlama i ≤ kesim olaylarını değiştirmez"
    short = df.iloc[:cut + 1].reset_index(drop=True)
    r3 = {"oi_t": raw["oi_t"][raw["oi_t"] <= t_cut], "oi": raw["oi"][raw["oi_t"] <= t_cut],
          "f_t": raw["f_t"][raw["f_t"] <= t_cut], "f_rate": raw["f_rate"][raw["f_t"] <= t_cut]}
    assert key(evs(short, r3)) == full, "kesilmiş seri aynı olayları verir"


# ---------------------------------------------------------------------------- 16. aggregate + katkı + defter
def _evs(name: str, family: str, rs, days, side: str = L.LONG, tf: str = "4h", fr=None) -> list[dict]:
    return [{"symbol": f"S{k % 3}", "tf": tf, "family": family, "name": name, "side": side,
             "t_ms": T0 + int(d) * DAY + (k % 6) * HOUR, "r": float(r), "cost_r": 0.05, "ctx": {"hacim": "normal"},
             "funding_r": (None if fr is None else fr[k % len(fr)])} for k, (r, d) in enumerate(zip(rs, days))]


def _span(rng, n: int, d0: int = 0, d1: int = 200) -> np.ndarray:
    days = rng.integers(d0, d1, n)
    days[0], days[-1] = d0, d1 - 1
    return days


def test_aggregate_excludes_control_and_contributions_label_katki_and_books():
    rng = np.random.default_rng(7)
    cfg = L.LabConfig()
    ev = []

    def case(name, side, rule_mean, ctrl, tf="4h", n_ctrl=300):
        rs, d = rng.normal(rule_mean, 1.0, 300), _span(rng, 300)
        ev.extend(_evs(name, "futures", rs, d, side, tf, fr=[-0.01, float("nan")]))
        if ctrl == "drift":                                  # kontrol = kural − 0.02 (aynı günler): fark pozitif, aralık 0'ı içerir
            ev.extend(_evs("CTRL_" + name, "control", rs - 0.02, d, side, tf))
        else:
            ev.extend(_evs("CTRL_" + name, "control", rng.normal(ctrl, 1.0, n_ctrl), _span(rng, n_ctrl), side, tf))
        ev.extend(_evs("PLACEBO_" + name, "placebo", rng.normal(-0.3, 1.0, 300), _span(rng, 300), side, tf))
    case("OI_BREAKOUT_20", L.LONG, 0.5, 0.0)                 # KESİN
    case("OI_BREAKOUT_20", L.SHORT, 0.5, 0.9)                # YOK (kontrol daha iyi)
    case("OI_REGIME_CONT", L.LONG, 0.5, "drift")             # VAR
    case("OI_REGIME_REVERT", L.LONG, 0.5, 0.0, n_ctrl=12)    # ÖLÇÜLEMEDİ
    case("OI_BREAKOUT_20", L.LONG, 0.5, 0.0, tf="1h")        # bilgi: aday olamaz
    ev += _evs("PLACEBO_RANDOM", "placebo", rng.normal(-2.0, 1.0, 300), _span(rng, 300), L.SHORT)
    ev += _evs("FUNDING_SWEEP_REVERSAL", "futures", rng.normal(0.5, 1.0, 300), _span(rng, 300), L.SHORT)   # eşi yok
    agg = L.aggregate(ev, cfg)
    no_ctrl = L.aggregate([e for e in ev if e["family"] != "control"], cfg)
    assert agg["tested"] == no_ctrl["tested"] and agg["candidate_rate"] == no_ctrl["candidate_rate"]
    assert agg["tf_summary"] == no_ctrl["tf_summary"], "kontrol tf özetine girmez"
    assert any(g["family"] == "control" and g["verdict"] != L.V_THIN for g in agg["groups"])
    g = lambda name, side, tf="4h": next(x for x in agg["groups"] if (x["name"], x["side"], x["tf"], x["context"]) == (name, side, tf, "HEPSİ"))  # noqa: E731
    assert g("FUNDING_SWEEP_REVERSAL", L.SHORT)["vs_placebo"] is None, "PLACEBO_RANDOM'a düşmez"
    assert g("FUNDING_SWEEP_REVERSAL", L.SHORT)["verdict"] != L.V_STRONG
    assert g("CTRL_OI_BREAKOUT_20", L.LONG)["vs_placebo"] is None, "kontrolün eşi yok"
    rows = {(r["tf"], r["name"], r["side"]): r for r in FL.contributions(ev, agg, cfg, ["4h", "1h"])}
    assert len(rows) == 16 and sum(1 for k in rows if k[0] == "4h") == 8, "4h'te her zaman 8 hipotez satırı"
    a, b = rows[("4h", "OI_BREAKOUT_20", L.LONG)], rows[("4h", "OI_BREAKOUT_20", L.SHORT)]
    c, d = rows[("4h", "OI_REGIME_CONT", L.LONG)], rows[("4h", "OI_REGIME_REVERT", L.LONG)]
    assert (a["katki"], b["katki"], c["katki"], d["katki"]) == (FL.KATKI_KESIN, FL.KATKI_YOK, FL.KATKI_VAR, FL.KATKI_NA)
    assert c["vs_control"]["IS"] == pytest.approx(0.02, abs=1e-9) and c["vs_control"]["ci95_OOS"][0] <= 0
    for r in (a, b, c):
        assert r["verdict"] == L.V_STRONG and r["vs_placebo"]["ci95_OOS"] is not None
    assert a["verdict_strict"] == L.V_STRONG and a["book"] == {"standard": True, "strict": True}
    assert b["book"] == {"standard": False, "strict": False} and b["note"] == FL.NOTE_PRICE_ONLY
    assert c["book"] == {"standard": True, "strict": False}, "VAR: standart aday, sıkı değil"
    assert d["book"] == {"standard": False, "strict": False}
    info = rows[("1h", "OI_BREAKOUT_20", L.LONG)]
    assert info["katki"] == FL.KATKI_KESIN and not info["primary"] and info["book"] == {"standard": False, "strict": False}
    assert a["funding_r"] == {"IS": -0.01, "OOS": -0.01, "unknown_share": 0.5}
    missing = rows[("4h", "FUNDING_SWEEP_REVERSAL", L.LONG)]
    assert missing["IS"] == {"n": 0} and missing["verdict"] == L.V_THIN and missing["katki"] == FL.KATKI_NA
    # render: kontrol grupları genel listelerde görünmez; vadeli bölüm ayrı
    rep = {**agg, "symbols": ["S0", "S1", "S2"], "timeframes": ["4h", "1h"], "seconds": 0}
    txt = L.render(rep)
    assert "CTRL_" not in txt and "VADELİ VERİ" not in txt
    rep["futures"] = {"mode": "rules", "version": FL.FUT_VERSION, "registry_sha": FL.FUT_REGISTRY_SHA, "coverage": {},
                      "contributions": list(rows.values()), "hypotheses": 8, "primary_tf": "4h"}
    txt = L.render(rep)
    assert "== VADELİ VERİ (OI/FONLAMA) — ön kayıtlı 8 hipotez · fut_v1 · sha " + FL.FUT_REGISTRY_SHA in txt
    assert FL.HYPOTHESIS_LINE in txt and "katkı KESİN" in txt and FL.NOTE_PRICE_ONLY in txt and "(bilgi)" in txt
    lines = FL.log_lines(rep)
    assert lines[0].startswith("FUT_REGISTRY ") and sum(1 for x in lines if x.startswith("FUT_RESULT ")) == 16
    assert all(len(x.encode("utf-8")) <= 4000 for x in lines)
    first = json.loads(next(x for x in lines if x.startswith("FUT_RESULT "))[len("FUT_RESULT "):])
    assert set(first) == {"tf", "name", "side", "IS", "OOS", "vs_placebo", "vs_control", "katki", "funding_r", "verdict",
                          "verdict_strict", "book", "symbols"}


# ---------------------------------------------------------------------------- 17. rastgele yürüyüş kalibrasyonu
def test_random_walk_with_independent_oi_and_funding_gives_no_strong_futures_candidate():
    evs = []
    for k in range(30):
        df = rw_prices(1300, 500 + k)
        raw = rw_raw(T0 - 40 * DAY, T0 + 1310 * H4, 500 + k)
        e, meta = L.process_series(df, f"R{k}/USDT", "4h", L.LabConfig(), catalog=False, algos=False, extras=False,
                                   futures="rules", fut_raw=raw)
        assert meta["futures"]["bars_oi_ok"] > 900
        evs += [dataclasses.asdict(x) for x in e]
    fams = {e["family"] for e in evs}
    assert fams == {"futures", "control", "placebo"}
    assert all(set(e["ctx"]) == {"hacim", "rsi", "trend", "volatilite", "oi_rejim", "oi_seviye", "fonlama"} for e in evs)
    agg = L.aggregate(evs, L.LabConfig())
    fut = [g for g in agg["groups"] if g["family"] == "futures"]
    assert fut and not [g for g in fut if g["verdict"] == L.V_STRONG and g["context"] == "HEPSİ"]
    assert not [g for g in fut if g["verdict_strict"] == L.V_STRONG]
    cr = agg["candidate_rate"]
    assert cr["real"] is not None and cr["placebo"] is not None and cr["real"] <= cr["placebo"] + 0.1, cr
    rows = FL.contributions(evs, agg, L.LabConfig(), ["4h"])
    assert not [r for r in rows if r["book"]["standard"] or r["book"]["strict"]]
    carry = [e["funding_r"] for e in evs]
    assert all(x is not None for x in carry), "vadeli ailelerin hepsine funding_r yazılır (bilinmeyen NaN)"


# ---------------------------------------------------------------------------- 18. kayıt sha'sı
def test_registry_sha_is_pinned_and_documented():
    again = hashlib.sha256(json.dumps(FL.FUT_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert FL.FUT_REGISTRY_SHA == again == PINNED_SHA, "kayıt değişti: fut_v2 + belge + yeni deneme sayısı gerekir"
    doc = (ROOT / "docs" / "FUTURES_OI_FUNDING_LAB.md").read_text(encoding="utf-8")
    assert PINNED_SHA in doc and "fut_v1" in doc
    assert FL.FUT_REGISTRY["hypotheses"] == [f"4h {r} {s} HEPSİ" for r in FL.RULES for s in (L.LONG, L.SHORT)]
    assert FL.FUT_REGISTRY["features"] == FF.FEATURE_CONSTANTS and FL.FUT_REGISTRY["warmup_days"] == FD.WARMUP_DAYS == 22
    tweak = json.loads(json.dumps(FL.FUT_REGISTRY))
    tweak["rules"]["FUNDING_SWEEP_REVERSAL"]["fund_rank"] = 0.95
    assert hashlib.sha256(json.dumps(tweak, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] != PINNED_SHA


def test_registry_pins_exit_and_cost_config_used_by_simulate():
    """#10 simulate(), #1/#9 simulate_rule() ile LabConfig'ten okur: varsayılan değişirse mühür de değişmeli."""
    ex, cfg = FL.FUT_REGISTRY["exit_cfg"], dataclasses.asdict(L.LabConfig())
    assert {k: ex[k] for k in ex if k not in ("tr", "rule_stop_too_far_atr")} == {k: cfg[k] for k in ex if k in cfg}
    assert set(ex) - set(cfg) == {"tr", "rule_stop_too_far_atr"}
    n = 5
    arr = {k: np.full(n, 100.0) for k in ("open", "high", "low", "close")}
    atr = np.full(n, 1.0)
    lim = ex["rule_stop_too_far_atr"]
    far = L.Event("X", "4h", "futures", "R", L.LONG, 0, 0, 100.0 - lim - 0.01, exit={"kind": "hold", "bars": 2})
    near = L.Event("X", "4h", "futures", "R", L.LONG, 0, 0, 100.0 - lim + 0.01, exit={"kind": "hold", "bars": 2})
    assert L.simulate_rule(far, arr, atr, {}, L.LabConfig()) == "STOP_TOO_FAR"
    assert L.simulate_rule(near, arr, atr, {}, L.LabConfig()) == ""


# ---------------------------------------------------------------------------- 19. komut satırı
def _cli():
    spec = importlib.util.spec_from_file_location("_signal_lab_cli_futures", ROOT / "scripts" / "signal_lab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_futures_guards_and_only_futures(monkeypatch, tmp_path, capsys):
    cli = _cli()
    seen: dict = {}

    def fake_run(**kw):
        seen.clear()
        seen.update(kw)
        return {"groups": [], "symbols": kw["symbols"], "timeframes": kw["tfs"]}
    monkeypatch.setattr(L, "run", fake_run)
    out = ["--out", str(tmp_path / "o")]
    arch = ["--source", "archive", "--tfs", "4h,1h,1d"] + out
    assert cli.main(["--futures", "rules", "--only-variations", "--variations", "CV000_EXAMPLE_BULL3"] + arch) == 2 and not seen
    assert cli.main(["--futures", "ctx", "--variations", "CV000_EXAMPLE_BULL3"] + arch) == 2 and not seen
    assert "--futures" in capsys.readouterr().err
    assert cli.main(["--futures", "probe", "--tfs", "4h"] + out) == 2 and not seen, "--source api (varsayılan)"
    assert "OI geçmişi yalnız arşivden: --source archive" in capsys.readouterr().err
    assert cli.main(["--futures", "rules", "--source", "archive", "--tfs", "4h,15m"] + out) == 2 and not seen
    assert cli.main(["--only-futures", "--source", "archive", "--tfs", "5m"] + out) == 2 and not seen
    assert cli.main(["--only-futures", "--futures", "ctx"] + arch) == 2 and not seen
    assert cli.main(["--only-futures"] + arch) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["futures"], seen["variations"]) == (False, False, False, "rules", [])
    printed = capsys.readouterr().out
    assert "FUT_REGISTRY" in printed and FL.FUT_REGISTRY_SHA in printed
    assert cli.main(["--futures", "ctx", "--no-catalog"] + arch) == 0
    assert (seen["catalog"], seen["algos"], seen["extras"], seen["futures"]) == (False, True, True, "ctx")
    assert cli.main(["--futures", "rules", "--offline", "--tfs", "4h"] + out) == 0 and seen["provider_factory"] is None
    capsys.readouterr()
    assert cli.main(["--offline", "--tfs", "4h"] + out) == 0 and seen["futures"] == "off"
    assert "FUT_" not in capsys.readouterr().out, "varsayılan koşu vadeli satır basmaz"


# ---------------------------------------------------------------------------- uçtan uca (sahte arşiv)
def test_end_to_end_rules_probe_offline_and_abort(tmp_path, monkeypatch):
    frames, raws, now = lab_world()
    arc = Archive(raws)
    logs: list[str] = []
    kw = dict(symbols=list(frames), tfs=["4h"], cache_dir=tmp_path / "c", cfg=L.LabConfig(min_is=5, min_oos=5, bootstrap_iters=200),
              days={"4h": 150}, jobs=1, catalog=False, algos=False, extras=False, now_ms=now, log=logs.append)
    rep = L.run(out_dir=tmp_path / "o", provider_factory=lambda: Klines(frames), futures="rules", futures_fetch=arc, **kw)
    assert any("/daily/metrics/AAAUSDT/" in u for u in arc.asked) and any("/monthly/fundingRate/BBBUSDT/" in u for u in arc.asked)
    assert sum(1 for x in logs if x.startswith("FUT_COV ")) == 2 and any(x.startswith("FUT_PROBE ") for x in logs)
    doc = _strict_json(tmp_path / "o" / "signal_lab_report.json")
    fu = doc["futures"]
    assert (fu["mode"], fu["version"], fu["registry_sha"], fu["hypotheses"], fu["primary_tf"]) == ("rules", "fut_v1", FL.FUT_REGISTRY_SHA, 8, "4h")
    assert set(fu["coverage"]) == {"AAA/USDT", "BBB/USDT"} and all(c["status"] == "OK" for c in fu["coverage"].values())
    assert [(r["name"], r["side"]) for r in fu["contributions"]] == [(n, s) for n in FL.RULES for s in (L.LONG, L.SHORT)]
    assert {g["family"] for g in doc["groups"]} <= {"futures", "control", "placebo"} and doc["events"] > 50
    assert all(m["futures"]["bars_oi_ok"] > 500 for m in doc["series"])
    with gzip.open(tmp_path / "o" / "signal_lab_events.csv.gz", "rt", encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
    assert head[-1] == "funding_r"
    txt = L.render(rep)
    assert "VADELİ VERİ" in txt and "kapsama: OK 2" in txt
    # ikinci koşu çevrimdışı: yalnız önbellek (ağ YOK), aynı sonuç
    monkeypatch.setattr(L, "_http_get", lambda url, timeout=60.0: pytest.fail(f"ağ çağrısı: {url}"))
    rep2 = L.run(out_dir=tmp_path / "o2", provider_factory=None, futures="rules", **kw)
    strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]  # noqa: E731
    assert strip(rep2["futures"]["contributions"]) == strip(rep["futures"]["contributions"])
    # yoklama: P1–P7 + kör sayım, hüküm yok
    arc.asked.clear()
    rp = L.run(out_dir=tmp_path / "p", provider_factory=lambda: Klines(frames), futures="probe", futures_fetch=arc, **kw)
    assert set(rp["futures"]["probe"]) == {"P1", "P2", "P3", "P4", "P5", "P6", "P7"} and "groups" not in rp
    b = rp["futures"]["blind"]["4h"]
    assert len(b["rows"]) == 24 and b["bars_oi_ok"] > 1000 and sum(r["IS"] + r["OOS"] for r in b["rows"]) > 50
    assert all(r.get("note") == FL.THIN_BY_DESIGN for r in b["rows"] if r["name"] in FL.RULES and (r["IS"] < 30 or r["OOS"] < 20))
    assert not (tmp_path / "p" / "signal_lab_events.csv.gz").exists() and _strict_json(tmp_path / "p" / "signal_lab_report.json")
    lines = FL.log_lines(rp)
    assert sum(1 for x in lines if x.startswith("FUT_COUNT ")) == 24 and "kör sayım 4h" in L.render(rp)
    # bağlantı yok: vadeli arşiv indirilemez → rapor YAZILMAZ
    with pytest.raises(FD.FuturesDataUnavailable, match="ÇALIŞTIRILMADI"):
        L.run(out_dir=tmp_path / "dead", provider_factory=lambda: Klines(frames), futures="rules", futures_fetch=Archive(raws, dead=True),
              **{**kw, "cache_dir": tmp_path / "c2"})
    assert not (tmp_path / "dead" / "signal_lab_report.json").exists()
    with pytest.raises(ValueError, match="--futures"):
        L.run(out_dir=tmp_path / "x", provider_factory=None, futures="rules", **{**kw, "tfs": ["15m"]})
    with pytest.raises(ValueError, match="kipi"):
        L.run(out_dir=tmp_path / "x", provider_factory=None, futures="on", **kw)


# ---------------------------------------------------------------------------- fut_v2 (kalabalık) eklenince fut_v1 aynı kalır
#: Kalabalık laboratuvarından (fut_v2, 2026-09-30) ÖNCEKİ kodla (676c180) hesaplanan özetler: fut_v1 kiplerinin (probe/rules/
#: ctx) raporu, render'ı, günlük satırları, indirdiği URL'ler ve olay CSV'si DEĞİŞMEZ. Olay CSV'si 9 haneye yuvarlanarak
#: özetlenir (numpy çekirdeklerinin CPU'ya bağlı son-ULP gürültüsü; `test_signal_lab._csv_digest`). P7 (hız) ve süreler hariç.
GOLDEN_FUT = {
    "probe": {"report": "a5090dbcd600fe5e", "render": "f25ceae9d04c3752", "log_lines": "51898cb5d6763036", "logs": "096a58212e64f046",
              "urls": "4105df34717487e0"},
    "rules": {"report": "1bd61f6b9437edf4", "render": "44724ef393ff0edc", "log_lines": "1bc8577b6e6abdf5", "logs": "19c4c4e0b816521c",
              "events_csv": "743e161c1f6d24c5", "urls": "f3d1f7e7af27b525"},
    "ctx": {"report": "45b169ba953f170a", "render": "ecbe6033f430fa11", "log_lines": "5d43d8a1cdbc3b8f", "logs": "19c4c4e0b816521c",
            "events_csv": "2b93fb922f2d21aa", "urls": "f3d1f7e7af27b525"},
}

_GOLDEN_FUT_CODE = r'''
import gzip, hashlib, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
import test_futures_lab as T
import test_signal_lab as TS
from tradingbot import futures_lab as FL
from tradingbot import signal_lab as L

sha = lambda b: hashlib.sha256(b).hexdigest()[:16]

def digest(mode, tmp, algos):
    frames, raws, now = T.lab_world()
    arc = T.Archive(raws)
    logs = []
    kw = dict(symbols=list(frames), tfs=["4h"], cache_dir=tmp / "c", cfg=L.LabConfig(min_is=5, min_oos=5, bootstrap_iters=200),
              days={"4h": 150}, jobs=1, catalog=False, algos=algos, extras=algos, now_ms=now, log=logs.append)
    rep = L.run(out_dir=tmp / "o", provider_factory=lambda: T.Klines(frames), futures=mode, futures_fetch=arc, **kw)
    doc = json.loads((tmp / "o" / "signal_lab_report.json").read_text(encoding="utf-8"))
    doc.pop("seconds")
    shown = dict(rep, seconds=0)
    if mode == "probe":
        doc["futures"]["probe"].pop("P7")
        shown["futures"] = {**shown["futures"], "probe": {k: v for k, v in shown["futures"]["probe"].items() if k != "P7"}}
    out = {"report": sha(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()), "render": sha(L.render(shown).encode()),
           "log_lines": sha("\n".join(FL.log_lines(shown)).encode()),
           "logs": sha("\n".join(x for x in logs if x.startswith("FUT_") and '"item":"P7"' not in x).encode())}
    p = tmp / "o" / "signal_lab_events.csv.gz"
    if p.exists():
        out["events_csv"] = TS._csv_digest(gzip.decompress(p.read_bytes()))
    out["urls"] = sha("\n".join(sorted(arc.asked)).encode())
    return out

res = {}
for mode, algos in (("probe", False), ("rules", False), ("ctx", True)):
    with tempfile.TemporaryDirectory() as d:
        res[mode] = digest(mode, Path(d), algos)
print(json.dumps({"digests": res, "crowd_modules": sorted(m for m in sys.modules if "crowd_" in m)}))
'''


def test_fut_v1_modes_are_byte_identical_to_pre_crowd_code_and_never_import_crowd_modules():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", _GOLDEN_FUT_CODE], capture_output=True, text=True, check=True, cwd=str(ROOT))
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["crowd_modules"] == [], "fut_v1 kipleri crowd_* modüllerini içe aktarmaz"
    assert res["digests"] == GOLDEN_FUT, res["digests"]


def test_fut_v1_seal_and_funding_carry_default_unchanged_by_crowd_lab():
    import inspect
    from tradingbot import crowd_lab as CL
    again = hashlib.sha256(json.dumps(FL.FUT_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert FL.FUT_REGISTRY_SHA == again == PINNED_SHA and CL.CROWD_REGISTRY_SHA != PINNED_SHA
    assert FL.FUT_VERSION == "fut_v1" and CL.CROWD_VERSION == "fut_v2"
    assert inspect.signature(FL.funding_carry).parameters["names"].default is FL.FUT_NAMES
    assert not {k for k in FL.FUT_REGISTRY if "crowd" in k.lower()}, "fut_v1 kaydına kalabalık girmez"
