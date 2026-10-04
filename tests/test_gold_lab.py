# -*- coding: utf-8 -*-
"""Altın laboratuvarı gold_v1 (docs/GOLD_LAB_V1.md; 2026-10-04): ön kayıt mührü ve kaydın belgeyi kapsaması, pivot teyit
gecikmesi, GELECEĞE BAKMAMA (her varyant), 16 varyantın elle kurulmuş serilerde belgeye uygun stop/tetikle tetiklenmesi,
SHORT = LONG'un aynası, HTF'nin son KAPANMIŞ 4h barı, seans sınırları, SMC iptal ve order block / pivot başına tek sinyal,
plasebo (belirlenimci, anahtar, oran, stop), varyant başına RR/süre, aylık ölçü (işlemsiz ay, eşzamanlılık, aggregate kesimi),
spot mikro saniye, Dukascopy bi5 çözümü ve kapsama, uçtan uca (sahte arşiv, ağ YOK) ve komut satırı.
Yalnız sentetik veri; gerçek altın fiyatı YOK."""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import math
import urllib.request
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import gold_lab as G
from tradingbot import signal_lab as L
from tradingbot.timeframes import tf_ms

ROOT = Path(__file__).resolve().parents[1]
T0 = 1_704_067_200_000                    # 2024-01-01 00:00 UTC (Pazartesi)
HOUR = 3_600_000
DAY = 86_400_000

#: ön kayıt mührü — bir kural sabiti, kural metni, plasebo tanımı, maliyet/istatistik ayarı, veri penceresi ya da okunuş
#: değişirse bu test KIRILIR (yeni sürüm + belge + yeni deneme sayısı; sonuç görüldükten sonra gevşetme YOK)
PINNED_SHA = "4bec7df7721ea240"


# ---------------------------------------------------------------------------- yardımcılar
def frame(rows, tf="1h", t0=T0) -> pd.DataFrame:
    step = tf_ms(tf)
    o, h, lo, c = (np.array([r[k] for r in rows], dtype=float) for k in range(4))
    ts = t0 + np.arange(len(rows), dtype=np.int64) * step
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 100.0, "close_time": ts + step - 1})


def flat(n, px=100.0, rng=1.0):
    return [[px, px + rng, px - rng, px] for _ in range(n)]


def synth(n, tf="1h", seed=1, sub=8, t0=T0, vol=0.004) -> pd.DataFrame:
    """Rastgele yürüyüş (mum içi yol; açılış = önceki kapanış, boşluk yok)."""
    rnd = np.random.default_rng(seed)
    step = tf_ms(tf)
    path = 2000.0 * np.exp(np.cumsum(rnd.normal(0, vol / np.sqrt(sub), n * sub))).reshape(n, sub)
    o = np.r_[2000.0, path[:-1, -1]]
    ts = t0 + np.arange(n, dtype=np.int64) * step
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, path.max(1)), "low": np.minimum(o, path.min(1)),
                         "close": path[:, -1], "volume": rnd.uniform(50, 150, n), "close_time": ts + step - 1})


def sine(n, tf, period, t0=T0, amp=10.0) -> pd.DataFrame:
    """Hızlı dalga (EMA kesişimleri) + yavaş dalga (4h eğilimi iki yönde de olsun: HTF süzgeci iki tarafta da geçsin)."""
    i = np.arange(n)
    c = 120 + amp * np.sin(2 * np.pi * i / period) + 40 * np.sin(2 * np.pi * i / (period * 6.25)) + 0.7 * np.sin(2 * np.pi * i / 7.3)
    o = np.r_[c[0], c[:-1]]
    rows = [[o[k], max(o[k], c[k]) + 0.3, min(o[k], c[k]) - 0.3, c[k]] for k in range(n)]
    return frame(rows, tf, t0)


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    m = df.copy()
    m["open"], m["high"], m["low"], m["close"] = -df["open"], -df["low"], -df["high"], -df["close"]
    return m


def key(evs):
    return sorted((e.name, e.side, e.i, e.t_ms, e.stop, e.trigger, e.family) for e in evs)


def evs_of(df, tf, name, side=None):
    return [e for e in G.gold_events(df, "SYN", tf) if e.name == name and (side is None or e.side == side)]


def atr_of(df):
    return L.indicators(df)["atr"]


# ---------------------------------------------------------------------------- mühür ve kayıt
def test_registry_sha_is_pinned_documented_and_sensitive_to_every_part():
    again = hashlib.sha256(json.dumps(G.GOLD_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert G.GOLD_REGISTRY_SHA == again == PINNED_SHA, "kayıt değişti: yeni sürüm + belge + yeni deneme sayısı gerekir"
    doc = (ROOT / "docs" / "GOLD_LAB_V1.md").read_text(encoding="utf-8")
    assert f"Ön kayıt mührü: GOLD_REGISTRY_SHA = {PINNED_SHA}" in doc and G.GOLD_VERSION == "gold_v1"
    R = G.GOLD_REGISTRY
    for path, val in ((("rules", "EMA_X_SWING", "rr"), 3.0), (("rules", "SMC_FVG", "max_hold_bars", "4h"), 31),
                      (("common", "pivot_k"), 2), (("common", "buffer_atr"), 0.2), (("common", "session", "minutes"), [420, 1201]),
                      (("placebo", "key_tr"), "x"), (("cost", "fee_pct"), 0.04), (("stats", "min_oos"), 19),
                      (("monthly", "risk_pct"), 1.0), (("data", "main", "start"), "2020-09-01"), (("readings_tr",), []),
                      (("data", "dukascopy", "coverage_min"), 0.9), (("sections", "existing", "catalog"), ["15m", "1h", "4h"])):
        tweak = json.loads(json.dumps(R))
        node = tweak
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        assert hashlib.sha256(json.dumps(tweak, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] != PINNED_SHA, path


def test_registry_covers_every_rule_of_the_document():
    R = G.GOLD_REGISTRY
    doc = (ROOT / "docs" / "GOLD_LAB_V1.md").read_text(encoding="utf-8")
    names = ["EMA_X_SWING", "EMA_X_ATR", "EMA_X_SWING_HTF", "EMA_X_SWING_SESSION", "SMC_BOS_OB", "SMC_SWEEP", "SMC_CHOCH", "SMC_FVG"]
    assert list(R["rules"]) == names and all(f"`{n}`" in doc for n in names)
    assert len(R["variants"]) == 16 and len(set(R["variants"])) == 16
    assert len(R["primary_cells"]) == 32 and all(c.endswith(" HEPSİ") for c in R["primary_cells"])
    for n in names[:4]:
        r = R["rules"][n]
        assert (r["family"], r["tfs"], r["rr"], r["max_hold_bars"], r["trigger_tr"]) == (
            "A_EMA", ["15m", "1h"], 2.5, {"15m": 96, "1h": 48}, "karar barının kapanışı (close[i])")
    for n in names[4:]:
        r = R["rules"][n]
        assert (r["family"], r["tfs"], r["rr"], r["max_hold_bars"]) == ("B_SMC", ["1h", "4h"], 3.0, {"1h": 72, "4h": 30})
    trig = {n: R["rules"][n]["trigger_tr"] for n in names[4:]}
    assert trig == {"SMC_BOS_OB": "OB_high", "SMC_SWEEP": "close[i]", "SMC_CHOCH": "close[i]", "SMC_FVG": "low[m]"}
    stops = {n: R["rules"][n]["stop_tr"] for n in names}
    assert stops["EMA_X_ATR"].startswith("close[i] ∓ 1,5 × ATR") and stops["SMC_BOS_OB"] == "OB_low − b"
    assert stops["SMC_SWEEP"] == "low[i] − b" and stops["SMC_FVG"] == "high[m−2] − b" and stops["SMC_CHOCH"].startswith("Son teyitli")
    assert [R["rules"][n]["filters"] for n in names[:4]] == [[], [], ["HTF"], ["SESSION"]]
    assert {n: R["rules"][n]["placebo_stop"] for n in names} == {
        "EMA_X_SWING": "SWING10", "EMA_X_ATR": "ATR", "EMA_X_SWING_HTF": "SWING10", "EMA_X_SWING_SESSION": "SWING10",
        "SMC_BOS_OB": "PIVOT", "SMC_SWEEP": "BAR", "SMC_CHOCH": "PIVOT", "SMC_FVG": "PIVOT"}
    c = R["common"]
    assert (c["pivot_k"], c["buffer_atr"], c["swing_bars"], c["setup_bars"], c["atr_stop"], c["fvg_body_atr"], c["ema"]) == (
        3, 0.1, 10, 20, 1.5, 1.0, [20, 50])
    assert "j+3" in c["pivot_confirm_tr"] and c["htf"]["tf"] == "4h" and c["htf"]["ema"] == 50 and "KAPANMIŞ" in c["htf"]["rule_tr"]
    s = c["session"]
    assert (s["minutes"], s["inclusive_start"], s["inclusive_end"]) == ([420, 1200], True, False) and "[07:00, 20:00)" in s["rule_tr"]
    assert "close < OB_low" in c["smc_cancel_tr"] and "close < high[m−2]" in c["smc_cancel_tr"]
    assert "gövdesi ≥ 1 ATR(m−1)" in c["fvg_displacement_tr"] and "order block" in c["one_per_tr"] and "pivot" in c["one_per_tr"]
    p = R["placebo"]
    assert '"<sembol>|<dilim>|<ad>|<yön>|<zaman damgası>"' in p["key_tr"] and "2^32" in p["key_tr"]
    assert "sinyal sayısı / bar sayısı" in p["rate_tr"] and set(p["stops"]) == set(names)
    cfg = L.LabConfig()
    assert (R["cost"]["fee_pct"], R["cost"]["slippage_bps"], R["cost"]["maker_info"]["fee_pct"], R["cost"]["maker_info"]["slippage_bps"]) == (
        cfg.fee_pct, cfg.slippage_bps, 0.02, 0.0) == (0.05, 3.0, 0.02, 0.0)
    tm = R["trade_model"]
    assert (tm["chase_atr"], tm["min_risk_atr"], tm["max_risk_atr"]) == (1.0, 0.1, 5.0)
    st = R["stats"]
    assert (st["split"], st["min_is"], st["min_oos"], st["strict_rule_tr"]) == (2 / 3, 30, 20, L.STRICT_RULE_TR)
    m = R["monthly"]
    assert (m["risk_pct"], m["risk_pct_info"], m["target_pct_month"]) == (0.5, 1.0, 1.0) and "GÜÇLÜ ADAY" in m["meets_target_tr"]
    d = R["data"]
    assert (d["main"]["symbol"], d["main"]["start"], d["main"]["end"], d["main"]["tfs"]) == ("PAXGUSDT", "2020-08-01", "2026-09-30",
                                                                                          ["15m", "1h", "4h", "1d"])
    assert d["venue"]["symbols"] == {"XAUUSDT": ["2025-12-01", "2026-09-30"], "PAXGUSDT": ["2025-03-01", "2026-09-30"]}
    assert (d["dukascopy"]["start"], d["dukascopy"]["end"], d["dukascopy"]["coverage_min"]) == ("2006-01-01", "2026-09-30", 0.95)
    sec = R["sections"]
    assert sec["main"]["tfs"] == sec["venue"]["tfs"] == sec["dukascopy"]["tfs"] == ["15m", "1h", "4h"]
    assert (sec["existing"]["catalog"], sec["existing"]["extras"], sec["existing"]["algos"], sec["existing"]["variations"]) == (
        ["1h", "4h"], ["15m", "1h", "4h"], ["1h", "4h", "1d"], ["1h", "4h"])
    assert G.NOTE_VENUE in sec["venue"]["tr"] and G.NOTE_DUKA in sec["dukascopy"]["tr"] and R["trials"]["primary_cells"] == 32
    assert R["readings_tr"] == list(G.READINGS_TR) and len(R["readings_tr"]) >= 15
    # modül sabitleri kayıtla aynı (kayıt metin değil, kodun kullandığı değer)
    assert (G.PIVOT_K, G.BUF_ATR, G.SWING_BARS, G.SETUP_BARS, G.ATR_STOP, G.FVG_BODY_ATR, list(G.SESSION_MIN)) == (
        c["pivot_k"], c["buffer_atr"], c["swing_bars"], c["setup_bars"], c["atr_stop"], c["fvg_body_atr"], s["minutes"])


# ---------------------------------------------------------------------------- pivot, HTF, seans
def test_pivot_needs_strictly_greater_neighbours_and_is_visible_only_from_j_plus_3():
    rows = flat(30)
    rows[10][1] = 103.0                                     # swing yüksek j=10
    rows[15][2] = 97.0                                      # swing düşük j=15
    rows[20][1] = 101.0                                     # eşit yükseklik: pivot DEĞİL
    df = frame(rows)
    ph, pl = G.swing_pivots(df["high"], df["low"])
    assert list(np.flatnonzero(ph)) == [10] and list(np.flatnonzero(pl)) == [15]
    last_h, _ = G.confirmed(ph)
    last_l, _ = G.confirmed(pl)
    assert all(last_h[i] == -1 for i in range(13)) and all(last_h[i] == 10 for i in range(13, 30))
    assert all(last_l[i] == -1 for i in range(18)) and last_l[18] == 15
    rows[12][1] = 103.0                                     # j+2'de eşit yükseklik → j=10 artık pivot değil
    ph2, _ = G.swing_pivots(frame(rows)["high"], frame(rows)["low"])
    assert not ph2[10]


def test_htf_uses_only_the_last_closed_4h_bar():
    n = 272
    c = 100 + 0.01 * np.arange(n)                           # yavaş yükseliş: 4h kapanış > EMA50(4h)
    c[259] = 110.0                                          # 4h kutusu 64'ün (bar 256..259) son kapanışı
    c[260:] = 90.0                                          # kutu 65 ve sonrası: kapanış EMA'nın altında
    df = frame([[x, x + 0.1, x - 0.1, x] for x in c])
    side = G.htf_side(df["timestamp"], HOUR, df["close"])
    assert side[258] == 1 and side[259] == 1                # T = kutu 64'ün sonu: kutu 64 (110) kapanmış sayılır (eşitlik)
    assert side[260] == 1 and side[261] == 1 and side[262] == 1   # oluşan kutu 65 (90) GÖRÜLMEZ; son kapanmış = kutu 64
    assert side[263] == -1                                  # kutu 65 kapandı (90 < EMA)
    assert (side[:199] == 0).all()                          # EMA50(4h) için 50 kapanmış 4h barı yok
    c2 = c.copy()
    c2[260:262] = 1000.0                                    # oluşan kutunun içi değişse de karar değişmez
    df2 = frame([[x, x + 0.1, x - 0.1, x] for x in c2])
    assert np.array_equal(G.htf_side(df2["timestamp"], HOUR, df2["close"])[:263], side[:263])


def test_session_window_is_07_inclusive_to_20_exclusive_on_decision_time():
    day = T0
    at = lambda hh, mm: day + (hh * 60 + mm) * 60_000  # noqa: E731
    got = G.in_session(np.array([at(6, 45), at(6, 59), at(7, 0), at(12, 0), at(19, 45), at(19, 59), at(20, 0), at(23, 0), at(0, 0)]))
    assert list(got) == [False, False, True, True, True, True, False, False, False]
    df = sine(3000, "15m", 240)                             # 15m: bar 06:45 açılır → 07:00'da kapanır → seans İÇİ
    sw = {e.i: e for e in evs_of(df, "15m", "EMA_X_SWING")}
    ss = evs_of(df, "15m", "EMA_X_SWING_SESSION")
    exp = {i for i, e in sw.items() if 420 <= ((e.t_ms // 60_000) % 1440) < 1200}
    assert {e.i for e in ss} == exp and 0 < len(exp) < len(sw)
    assert all(e.t_ms == int(df["timestamp"][e.i]) + tf_ms("15m") for e in ss)


# ---------------------------------------------------------------------------- 16 varyant elle kurulmuş serilerde
@pytest.mark.parametrize("tf,period", [("15m", 240), ("1h", 120)])
def test_ema_family_fires_with_doc_stops_triggers_and_filters(tf, period):
    df = sine(4000 if tf == "15m" else 1500, tf, period)
    step = tf_ms(tf)
    c, lo = df["close"].to_numpy(), df["low"].to_numpy()
    atr = atr_of(df)
    e20 = df["close"].ewm(span=20, adjust=False, min_periods=20).mean().to_numpy()
    e50 = df["close"].ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()
    up = {i for i in range(1, len(df)) if e20[i] > e50[i] and e20[i - 1] <= e50[i - 1] and i >= 9}
    dn = {i for i in range(1, len(df)) if e20[i] < e50[i] and e20[i - 1] >= e50[i - 1] and i >= 9}
    for name in G.EMA_NAMES:
        assert evs_of(df, tf, name, "LONG") and evs_of(df, tf, name, "SHORT"), name
    sw = evs_of(df, tf, "EMA_X_SWING")
    assert {e.i for e in sw if e.side == "LONG"} == up and {e.i for e in sw if e.side == "SHORT"} == dn
    hi = df["high"].to_numpy()
    for e in sw:
        ext = lo[e.i - 9:e.i + 1].min() - 0.1 * atr[e.i] if e.side == "LONG" else hi[e.i - 9:e.i + 1].max() + 0.1 * atr[e.i]
        assert math.isclose(e.stop, ext, rel_tol=0, abs_tol=1e-9) and e.trigger == c[e.i] and e.target is None
        assert e.family == "gold" and e.t_ms == int(df["timestamp"][e.i]) + step
    for e in evs_of(df, tf, "EMA_X_ATR"):
        exp = c[e.i] - 1.5 * atr[e.i] if e.side == "LONG" else c[e.i] + 1.5 * atr[e.i]
        assert math.isclose(e.stop, exp, abs_tol=1e-9) and e.trigger == c[e.i]
    # HTF: bağımsız hesap (pandas yeniden örnekleme, sol etiket, son KAPANMIŞ kutu)
    idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    c4 = df.set_index(idx)["close"].resample("4h", label="left", closed="left").last().dropna()
    ema4 = c4.ewm(span=50, adjust=False, min_periods=50).mean()
    starts = ((c4.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy(dtype=np.int64)
    htf = evs_of(df, tf, "EMA_X_SWING_HTF")
    exp = set()
    for e in sw:
        k = np.searchsorted(starts + 4 * HOUR, e.t_ms, side="right") - 1
        if k >= 0 and not math.isnan(ema4.iloc[k]):
            if (e.side == "LONG" and c4.iloc[k] > ema4.iloc[k]) or (e.side == "SHORT" and c4.iloc[k] < ema4.iloc[k]):
                exp.add((e.i, e.side))
    assert {(e.i, e.side) for e in htf} == exp and 0 < len(exp) < len(sw)
    assert all(math.isclose(e.stop, next(s.stop for s in sw if (s.i, s.side) == (e.i, e.side)), abs_tol=0) for e in htf)


def _bos_rows(n=150):
    rows = flat(n)
    rows[60] = [100, 103, 99, 100]                          # swing yüksek H = 103
    rows[70] = [100, 101, 97, 100]                          # swing düşük
    rows[75] = [100.5, 101, 99, 99.5]                       # SON ayı mumu → OB [99, 101]
    rows[80] = [100, 104.5, 99.5, 104]                      # BOS: close 104 > 103, close[79] = 100 ≤ 103
    for k in range(81, 85):
        rows[k] = [104, 105, 103, 104]
    rows[85] = [100.6, 102.2, 100.5, 102]                   # İLK dokunuş: low ≤ 101, close ≥ 99 (boğa)
    for k in range(86, n):
        rows[k] = [101, 101.5, 100.5, 101]
    return rows


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_smc_bos_ob_fires_once_per_order_block_with_doc_stop_and_trigger(tf):
    rows = _bos_rows()
    rows[96] = [101, 104.3, 100.5, 104]                     # ikinci BOS, AYNI order block (75) → sinyal YOK
    df = frame(rows, tf)
    ev = evs_of(df, tf, "SMC_BOS_OB", "LONG")
    atr = atr_of(df)
    assert [e.i for e in ev] == [85]
    assert math.isclose(ev[0].stop, 99 - 0.1 * atr[85], abs_tol=1e-12) and ev[0].trigger == 101 and ev[0].target is None
    # iki BOS aynı order block'a bekleyen iki kurulum açar; aynı barda dokunuşta yalnız BİR sinyal
    rows = _bos_rows()
    rows[81] = [102.5, 103, 102, 102.5]
    rows[82] = [102.5, 104.2, 102.4, 104]
    ev = evs_of(frame(rows, tf), tf, "SMC_BOS_OB", "LONG")
    assert [e.i for e in ev] == [85]


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_smc_bos_ob_cancellation_and_20_bar_window(tf):
    rows = _bos_rows()
    rows[83] = [104, 104, 98.8, 98.9]                       # girişten önce close < OB_low → iptal (low ≤ 101 ama close < 99)
    rows[84] = [101, 101.5, 100.5, 101]                     # H'nin altında kalır: yeni BOS yok
    assert evs_of(frame(rows, tf), tf, "SMC_BOS_OB", "LONG") == []
    rows[84] = [104, 105, 103, 104]                         # H'yi yeniden kırarsa YENİ kurulum (OB = 83, son ayı mumu) → 85'te sinyal
    ev = evs_of(frame(rows, tf), tf, "SMC_BOS_OB", "LONG")
    assert [(e.i, e.trigger) for e in ev] == [(85, 104.0)]
    for touch, fired in ((100, True), (101, False)):        # pencere i+1..i+20 = 81..100
        rows = _bos_rows()
        for k in range(81, touch):
            rows[k] = [104, 105, 103, 104]
        rows[touch] = [100.6, 102.2, 100.5, 102]
        for k in range(touch + 1, len(rows)):
            rows[k] = [104, 105, 103, 104]
        ev = evs_of(frame(rows, tf), tf, "SMC_BOS_OB", "LONG")
        assert ([e.i for e in ev] == [touch]) is fired, touch
    rows = _bos_rows()
    rows[75] = [100, 101, 99, 100]                          # ayı mumu yok → kurulum yok
    assert evs_of(frame(rows, tf), tf, "SMC_BOS_OB", "LONG") == []


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_smc_sweep_uses_each_confirmed_pivot_once(tf):
    rows = flat(140)
    rows[100] = [100, 101, 97, 100]                         # swing düşük S = 97 (teyit 103)
    rows[110] = [100, 101, 96.5, 100]                       # süpürme: low < 97, close > 97 → LONG
    rows[111] = [100, 101, 96.4, 100]                       # aynı pivot ikinci kez → YOK (111 kendisi yeni pivot)
    rows[120] = [100, 101, 96.3, 100]                       # yeni pivot (111, 96.4) süpürülür → LONG
    df = frame(rows, tf)
    atr = atr_of(df)
    ev = evs_of(df, tf, "SMC_SWEEP", "LONG")
    assert [e.i for e in ev] == [110, 120]
    assert math.isclose(ev[0].stop, 96.5 - 0.1 * atr[110], abs_tol=1e-12) and ev[0].trigger == 100
    assert math.isclose(ev[1].stop, 96.3 - 0.1 * atr[120], abs_tol=1e-12)
    rows2 = [r[:] for r in rows]
    rows2[110] = [100, 101, 96.5, 96.8]                     # kapanış S'nin altında → süpürme değil
    assert 110 not in [e.i for e in evs_of(frame(rows2, tf), tf, "SMC_SWEEP", "LONG")]


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_smc_choch_requires_descending_highs_and_lows(tf):
    rows = flat(120)
    rows[60] = [100, 106, 99, 100]
    rows[65] = [100, 101, 94, 100]
    rows[70] = [100, 104, 99, 100]                          # yüksekler azalan: 106 → 104
    rows[75] = [100, 101, 93, 100]                          # düşükler azalan: 94 → 93
    rows[85] = [100, 105, 99, 104.5]                        # close > 104 ve close[84] ≤ 104
    df = frame(rows, tf)
    atr = atr_of(df)
    ev = evs_of(df, tf, "SMC_CHOCH", "LONG")
    assert [e.i for e in ev] == [85]
    assert math.isclose(ev[0].stop, 93 - 0.1 * atr[85], abs_tol=1e-12) and ev[0].trigger == 104.5
    rows[75] = [100, 101, 95, 100]                          # düşükler yükselen → yapı yok
    assert evs_of(frame(rows, tf), tf, "SMC_CHOCH", "LONG") == []


def _fvg_rows(n=120):
    rows = flat(n)
    rows[81] = [100, 104.5, 99.8, 104]                      # boğa, gövde 4 ≥ ATR(81)
    rows[82] = [104, 105, 101.5, 104.5]                     # low 101.5 > high[80] 101 → bölge [101, 101.5]
    for k in range(83, 86):
        rows[k] = [104, 105, 103, 104]
    rows[86] = [104, 104, 101.2, 102]                       # İLK dokunuş: low ≤ 101.5, close ≥ 101
    for k in range(87, n):
        rows[k] = [104, 105, 103, 104]
    return rows


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_smc_fvg_fires_cancels_and_needs_displacement(tf):
    df = frame(_fvg_rows(), tf)
    atr = atr_of(df)
    ev = evs_of(df, tf, "SMC_FVG", "LONG")
    assert [e.i for e in ev] == [86] and atr[81] <= 4
    assert math.isclose(ev[0].stop, 101 - 0.1 * atr[86], abs_tol=1e-12) and ev[0].trigger == 101.5
    rows = _fvg_rows()
    rows[84] = [104, 104, 100.5, 100.8]                     # girişten önce close < high[m−2] → iptal
    assert evs_of(frame(rows, tf), tf, "SMC_FVG", "LONG") == []
    rows = _fvg_rows()
    rows[81] = [100, 104.5, 99.8, 101.5]                    # gövde 1,5 < ATR(81) → FVG yok
    assert evs_of(frame(rows, tf), tf, "SMC_FVG", "LONG") == []


def test_every_one_of_the_16_variants_fires_on_both_sides():
    seen = set()
    for tf, df in (("15m", sine(4000, "15m", 240)), ("1h", sine(1500, "1h", 120)), ("1h", synth(3000, "1h", 5)),
                   ("4h", synth(3000, "4h", 6))):
        for e in G.gold_events(df, "SYN", tf):
            seen.add((e.name, tf, e.side))
    want = {(n, tf, s) for n, r in G.RULES.items() for tf in r["tfs"] for s in ("LONG", "SHORT")}
    assert want <= seen, sorted(want - seen)


# ---------------------------------------------------------------------------- aynalık ve geleceğe bakmama
@pytest.mark.parametrize("tf,n,seed", [("15m", 3000, 11), ("1h", 2500, 12), ("4h", 2500, 13)])
def test_short_is_the_exact_mirror_of_long(tf, n, seed):
    df = synth(n, tf, seed)
    a, b = G.gold_events(df, "SYN", tf), G.gold_events(mirror(df), "SYN", tf)
    flip = {"LONG": "SHORT", "SHORT": "LONG"}
    assert a and key(a) == sorted((e.name, flip[e.side], e.i, e.t_ms, -e.stop, -e.trigger, e.family) for e in b)
    assert {e.side for e in a} == {"LONG", "SHORT"}
    one = {(nm, s): 1.0 for nm in G.names_for_tf(tf) for s in ("LONG", "SHORT")}    # p = 1: her bar → stoplar aynalı
    pa, _ = G.placebo_events(df.iloc[:400], "SYN", tf, {}, p=one)
    pb, _ = G.placebo_events(mirror(df.iloc[:400]), "SYN", tf, {}, p=one)
    assert pa and key(pa) == sorted((e.name, flip[e.side], e.i, e.t_ms, -e.stop, -e.trigger, e.family) for e in pb)


@pytest.mark.parametrize("tf,n,seed", [("15m", 3000, 21), ("1h", 2500, 22), ("4h", 2500, 23)])
def test_no_lookahead_for_every_variant_and_placebo(tf, n, seed):
    df = synth(n, tf, seed)
    p = {(nm, s): 0.03 for nm in G.names_for_tf(tf) for s in ("LONG", "SHORT")}
    full = G.gold_events(df, "SYN", tf)
    pfull, _ = G.placebo_events(df, "SYN", tf, {}, p=p)
    rnd = np.random.default_rng(seed)
    covered = set()
    for cut in (n // 2, n - 300, n - 40):
        mut = df.copy()
        f = rnd.uniform(0.7, 1.4, n - cut - 1)
        for col in ("open", "high", "low", "close"):
            mut.loc[cut + 1:, col] = mut.loc[cut + 1:, col].to_numpy() * f
        got = G.gold_events(mut, "SYN", tf)
        assert key([e for e in got if e.i <= cut]) == key([e for e in full if e.i <= cut]), cut
        pgot, _ = G.placebo_events(mut, "SYN", tf, {}, p=p)
        assert key([e for e in pgot if e.i <= cut]) == key([e for e in pfull if e.i <= cut]), cut
        covered |= {(e.name, e.side) for e in full if e.i <= cut}
    assert covered == {(nm, s) for nm in G.names_for_tf(tf) for s in ("LONG", "SHORT")}


# ---------------------------------------------------------------------------- plasebo
def test_placebo_is_deterministic_keyed_by_crc_and_has_the_rate_and_stops_of_the_doc():
    df = synth(6000, "1h", 31)
    ts = df["timestamp"].to_numpy()
    atr = atr_of(df)
    real = G.gold_events(df, "PAXGUSDT", "1h")
    sig = {(n, s): sum(1 for e in real if (e.name, e.side) == (n, s)) for n in G.names_for_tf("1h") for s in ("LONG", "SHORT")}
    a, probs = G.placebo_events(df, "PAXGUSDT", "1h", sig)
    b, _ = G.placebo_events(df, "PAXGUSDT", "1h", sig)
    assert key(a) == key(b) and all(math.isclose(probs[k], sig[k] / len(df)) for k in sig)
    for (name, side), pr in probs.items():
        mine = {e.i for e in a if e.name == "PLACEBO_" + name and e.side == side}
        u = np.array([zlib.crc32(f"PAXGUSDT|1h|{name}|{side}|{int(t)}".encode()) / 2 ** 32 for t in ts])
        assert mine <= set(np.flatnonzero(u < pr)), (name, side)
        sel = int((u < pr).sum())
        assert len(mine) >= sel - 60 and abs(sel - pr * len(df)) <= 5 * math.sqrt(pr * len(df)) + 3, (name, side)
    lo, hi, c = df["low"].to_numpy(), df["high"].to_numpy(), df["close"].to_numpy()
    last_l = G.confirmed(G.swing_pivots(hi, lo)[1])[0]
    last_h = G.confirmed(G.swing_pivots(hi, lo)[0])[0]
    for e in a:
        i, b_ = e.i, 0.1 * atr[e.i]
        kind = G.RULES[e.name[8:]]["placebo_stop"]
        L_ = e.side == "LONG"
        exp = {"SWING10": lo[i - 9:i + 1].min() - b_ if L_ else hi[i - 9:i + 1].max() + b_,
               "ATR": c[i] - 1.5 * atr[i] if L_ else c[i] + 1.5 * atr[i],
               "BAR": lo[i] - b_ if L_ else hi[i] + b_,
               "PIVOT": lo[last_l[i]] - b_ if L_ else hi[last_h[i]] + b_}[kind]
        assert math.isclose(e.stop, exp, abs_tol=1e-9) and e.trigger == c[i] and e.family == "placebo"
    big, _ = G.placebo_events(synth(20000, "1h", 32), "SYN", "1h", {}, p={("SMC_SWEEP", "LONG"): 0.05}, names=["SMC_SWEEP"])
    assert abs(len(big) - 1000) < 5 * math.sqrt(1000), len(big)    # p × bar (stop hesaplanamayan ilk birkaç bar hariç)


# ---------------------------------------------------------------------------- simülasyon, aylık ölçü
def test_process_applies_each_variants_rr_and_hold_and_counts(monkeypatch):
    seen = {}
    orig = L.simulate

    def spy(ev, arr, atr, cfg):
        if cfg.fee_pct == L.LabConfig().fee_pct:
            seen.setdefault((G.base_name(ev.name), ev.tf), set()).add((cfg.default_rr, cfg.max_hold_bars))
        return orig(ev, arr, atr, cfg)
    monkeypatch.setattr(L, "simulate", spy)
    for tf, n in (("15m", 3000), ("1h", 2500), ("4h", 1500)):
        evs, meta = G.process(synth(n, tf, 41), "SYN", tf, L.LabConfig())
        for e in evs:
            rr, hold = G.RULES[G.base_name(e["name"])]["rr"], G.RULES[G.base_name(e["name"])]["max_hold_bars"][tf]
            assert e["hold"] <= hold and (e["exit_reason"] != "TIME" or e["hold"] == hold)
            if e["exit_reason"] == "TARGET":                # boşluksuz seri: hedef tam RR × gerçek risk
                assert math.isclose(e["r"] + e["cost_r"], rr, abs_tol=1e-9)
            assert e["entry_ms"] == e["t_ms"] and e["exit_ms"] == e["t_ms"] + e["hold"] * tf_ms(tf)
            assert e["r_maker"] is not None and e["r_maker"] > e["r"]
        for nm in G.names_for_tf(tf):
            for s in ("LONG", "SHORT"):
                c = meta["counts"][nm][s]
                assert c["signals"] == c["trades"] + sum(c["skipped"].values())
    want = {(n, tf): {(r["rr"], r["max_hold_bars"][tf])} for n, r in G.RULES.items() for tf in r["tfs"]}
    assert seen == want
    assert G.variant_cfg(L.LabConfig(), "PLACEBO_SMC_FVG", "4h").max_hold_bars == 30


def _ev(t, r, hold_h=48, tf="1h", name="SMC_SWEEP", side="LONG"):
    return {"tf": tf, "family": "gold", "name": name, "side": side, "t_ms": t, "r": r, "entry_ms": t, "exit_ms": t + hold_h * HOUR}


def test_monthly_metric_counts_zero_months_share_and_concurrency():
    ms = lambda s: G.day_ms(s)  # noqa: E731
    cut, last = ms("2024-01-15"), ms("2024-05-01") - 1
    rows = [_ev(ms("2024-01-10"), 5.0), _ev(ms("2024-01-20"), 2.0), _ev(ms("2024-03-05"), -1.0),
            _ev(ms("2024-03-06"), 0.5, hold_h=12)]
    m = G.monthly_stats(rows, cut, last, iters=200)
    assert m["months"] == 4 and m["monthly_r"] == {"2024-01": 2.0, "2024-02": 0.0, "2024-03": -0.5, "2024-04": 0.0}
    assert m["trades"] == 3 and m["months_without_trades"] == 2
    assert math.isclose(m["mean_r_month"], 0.375) and math.isclose(m["mean_pct_month"], 0.1875)
    assert math.isclose(m["mean_pct_month_risk1"], 0.375) and m["share_months_ge_target"] == 0.25 and m["ci95_pct_month"] is None
    conc = m["concurrency"]
    assert conc["max"] == 2 and math.isclose(conc["mean"], round((48 + 48 + 12) * HOUR / (last - cut), 4))
    # uç uca işlemler eşzamanlı değildir (önce çıkış)
    assert G.concurrency([_ev(0, 1, 2), _ev(2 * HOUR, 1, 2)], 0, 4 * HOUR)["max"] == 1
    rows += [_ev(ms(f"2024-0{k}-02"), 1.0) for k in (2, 4)]
    m2 = G.monthly_stats(rows, cut, ms("2024-08-01") - 1, iters=300)
    m3 = G.monthly_stats(rows, cut, ms("2024-08-01") - 1, iters=300)
    assert m2["months"] == 7 and m2["ci95_pct_month"] == m3["ci95_pct_month"] and m2["ci95_pct_month"][0] <= m2["mean_pct_month"]
    assert G.meets_target({"mean_pct_month": 1.0}, [L.V_STRONG]) and not G.meets_target({"mean_pct_month": 0.99}, [L.V_STRONG])
    assert not G.meets_target({"mean_pct_month": 3.0}, [L.V_WEAK]) and not G.meets_target({"mean_pct_month": 3.0}, [L.V_STRONG, L.V_WEAK])


def test_main_report_has_32_cells_and_uses_the_aggregate_cutoff():
    cfg = L.LabConfig(bootstrap_iters=200)
    evs, metas = [], []
    for tf, n in (("15m", 6000), ("1h", 3000), ("4h", 1500)):
        e, m = G.process(synth(n, tf, 51), "SYN", tf, cfg)
        evs += e
        metas.append(m)
    rep = G.main_report(evs, metas, cfg)
    agg = L.aggregate(evs, cfg)
    assert rep["cutoff_ms"] == agg["cutoff_ms"] and len(rep["cells"]) == 32 and len(rep["both_sides"]) == 16
    assert [(c["tf"], c["name"], c["side"]) for c in rep["cells"]] == [
        (tf, n, s) for n, r in G.RULES.items() for tf in r["tfs"] for s in ("LONG", "SHORT")]
    checked = 0
    for c in rep["cells"]:
        if c["OOS"].get("n"):
            assert c["monthly"]["trades"] == c["OOS"]["n"], (c["tf"], c["name"], c["side"])
            checked += 1
        assert c["verdict"] in G.GOLD_REGISTRY["stats"]["verdicts"] and c["meets_target"] in (True, False)
        assert c["IS"]["n"] + c["OOS"]["n"] == c["trades"], (c["tf"], c["name"], c["side"])     # 10 işlemin altında da tutarlı
        assert c["signals"] >= c["trades"] and (c["placebo"] is None or "OOS" in c["placebo"])
    assert checked >= 20 and isinstance(rep["conclusion_tr"], str) and rep["candidate_rate_primary"]["real_tested"] >= 0
    md = G.render_md({"version": "gold_v1", "registry_sha": PINNED_SHA, "sections": ["main"], "main": rep, "data": {}})
    assert PINNED_SHA in md and "32 birincil hücre" in md and md.count("\n| 15m | EMA_X_SWING |") == 3


# ---------------------------------------------------------------------------- veri: spot, Dukascopy
def kline_zip(rows, header=False) -> bytes:
    buf = io.BytesIO()
    lines = (["open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,ignore"] if header else []) + [
        ",".join(str(x) for x in r) for r in rows]
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def krow(t_ms, tf, px=2000.0, micro=False):
    m = 1000 if micro else 1
    return [t_ms * m, px, px + 1, px - 1, px + 0.5, 3.0, (t_ms + tf_ms(tf)) * m - 1, 0, 1, 0, 0, 0]


def test_spot_loader_normalises_microseconds_falls_back_to_daily_and_clips():
    tf = "4h"
    step = tf_ms(tf)
    files = {}
    dec = [G.day_ms("2024-12-01") + k * step for k in range(31 * 6)]
    files[G.spot_url("PAXGUSDT", tf, "2024-12")] = kline_zip([krow(t, tf) for t in dec])                    # ms
    for d in ("2025-01-01", "2025-01-02"):                                                                   # ay dosyası YOK
        t = G.day_ms(d)
        files[G.spot_url("PAXGUSDT", tf, d)] = kline_zip([krow(t + k * step, tf, micro=True) for k in range(6)], header=True)
    feb = [G.day_ms("2025-02-01") + k * step for k in range(28 * 6)]
    rows = [krow(t, tf, micro=True) for t in feb] + [krow(feb[3], tf, px=2100.0, micro=True)]                 # tekrar
    files[G.spot_url("PAXGUSDT", tf, "2025-02")] = kline_zip(rows)
    asked = []
    get = lambda url: (asked.append(url), files.get(url))[1]  # noqa: E731
    s, e = G.window_ms("2024-12-31", "2025-02-10")
    df, q = G.load_spot("PAXGUSDT", tf, s, e, get)
    ts = df["timestamp"].to_numpy()
    assert ts[0] == G.day_ms("2024-12-31") and ts[-1] == G.day_ms("2025-02-10") + 5 * step and ts.max() < e
    assert (np.diff(ts) > 0).all() and (ts % step == 0).all() and (df["close_time"] - df["timestamp"] == step - 1).all()
    assert df.loc[df["timestamp"] == feb[3], "open"].item() == 2100.0                                           # son kayıt kalır
    assert q["daily_fallback"] == [{"month": "2025-01", "days": 2}] and q["missing_months"] == []
    assert q["n_gaps_over_24h"] == 1 and q["gaps_over_24h"][0]["from"].startswith("2025-01-02T20")
    assert sum(1 for u in asked if "/daily/" in u) == 31                                                      # ocak günleri
    with pytest.raises(ValueError):
        G.parse_spot_zip(kline_zip([[1735689600, 1, 2, 0.5, 1.5, 1, 1735703999, 0, 1, 0, 0, 0]]), tf)        # saniye: ms değil


def test_archive_cache_offline_and_404_markers(tmp_path):
    now = G.day_ms("2026-10-04")
    calls = []
    fetch = lambda url: (calls.append(url), None if "missing" in url else b"zip")[1]  # noqa: E731
    c = G.ArchiveCache(tmp_path, fetch=fetch, now_ms=now)
    old, new = "https://h/x/missing-1h-2026-08.zip", "https://h/x/missing-1h-2026-09-30.zip"
    assert c.get("https://h/x/ok-1h-2026-08.zip") == b"zip" and c.get(old) is None and c.get(new) is None
    assert c.path(old).with_name(c.path(old).name + ".missing").exists()
    assert not c.path(new).with_name(c.path(new).name + ".missing").exists()                # yayımlanmamış olabilir: kalıcı değil
    off = G.ArchiveCache(tmp_path, fetch=lambda u: pytest.fail("ağ"), offline=True, now_ms=now)
    assert off.get("https://h/x/ok-1h-2026-08.zip") == b"zip" and off.get(old) is None
    with pytest.raises(ConnectionError):
        off.get(new)


def bi5(records) -> bytes:
    raw = b"".join(np.array([r], dtype=G.DUKA_DTYPE).tobytes() for r in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_dukascopy_decode_resample_and_coverage(tmp_path):
    #            sn,   açılış,  kapanış,   düşük,  yüksek, hacim
    recs = [(0, 2050123, 2050500, 2049900, 2050600, 0.5), (60, 2050500, 2050100, 2050000, 2050700, 0.25),
            (120, 2050100, 2050100, 2050100, 2050100, 0.0), (900, 2050100, 2051000, 2050050, 2051100, 1.0),
            (3 * 3600 + 120, 2052000, 2052500, 2051900, 2052600, 0.75)]
    data = bi5(recs)
    d0 = G.day_ms("2024-01-02")
    m1 = G.decode_bi5(data, d0)
    assert list(m1["timestamp"]) == [d0, d0 + 60_000, d0 + 120_000, d0 + 900_000, d0 + (3 * 3600 + 120) * 1000]
    r0 = m1.iloc[0]
    assert (r0["open"], r0["close"], r0["low"], r0["high"], r0["volume"]) == (2050.123, 2050.5, 2049.9, 2050.6, 0.5)
    assert G.bi5_sanity(m1).all() and G.decode_bi5(b"", d0).empty
    with pytest.raises(ValueError):
        G.decode_bi5(lzma.compress(b"\x00" * 25, format=lzma.FORMAT_ALONE), d0)
    root = tmp_path / "duka"
    for d in ("2024-01-02", "2024-01-07"):                  # Salı + Pazar (Pazar okunur, kapsamada yok)
        p = G.duka_path(root, pd.Timestamp(d).date())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    assert G.duka_path(root, pd.Timestamp("2024-01-02").date()).as_posix().endswith("XAUUSD/2024/00/02/BID_candles_min_1.bi5")
    frames, q = G.load_dukascopy(root, "2024-01-01", "2024-01-07", ["15m", "1h", "4h"])
    assert q["days_read"] == 2 and q["rows_vol0"] == 2 and q["rows_bad"] == 0 and q["days_missing_file"] == 4
    f15 = frames["15m"]
    day = f15[f15["timestamp"] < d0 + DAY]
    assert list(day["timestamp"]) == [d0, d0 + 900_000, d0 + 3 * HOUR]                     # yalnız verisi olan kutular, sol etiket
    b0 = day.iloc[0]
    assert (b0["open"], b0["high"], b0["low"], b0["close"], b0["volume"]) == (2050.123, 2050.7, 2049.9, 2050.1, 0.75)
    h4 = frames["4h"][frames["4h"]["timestamp"] < d0 + DAY].iloc[0]
    assert (h4["timestamp"], h4["open"], h4["close"], h4["high"]) == (d0, 2050.123, 2052.5, 2052.6)
    man = root / "manifest.jsonl"
    lines = [{"date": "2024-01-01", "status": 404}, {"date": "2024-01-02", "status": 200}, {"date": "2024-01-03", "status": 200},
             {"date": "2024-01-04", "status": 0}, {"date": "2024-01-04", "status": 200}, {"date": "2024-01-05", "status": 503},
             {"date": "2024-01-07", "status": 200}]
    man.write_text("\n".join(json.dumps(x) for x in lines) + "\nbozuk satır\n", encoding="utf-8")
    cov = G.duka_coverage(root, "2024-01-01", "2024-01-07")
    assert (cov["weekdays"], cov["weekdays_ok"], cov["coverage"], cov["enough"]) == (5, 3, 0.6, False)
    full = G.duka_coverage(root, "2024-01-02", "2024-01-04")
    assert full["coverage"] == 1.0 and full["enough"]


# ---------------------------------------------------------------------------- uçtan uca (sahte arşiv, ağ YOK)
def _fake_archive(windows):
    """URL → sentetik zip (spot ve vadeli mumlar, fonlama); bilinmeyen dosya None (404)."""
    files = {}
    s, e = G.window_ms(*windows["main"])
    for k, tf in enumerate(("15m", "1h", "4h", "1d")):
        df = synth(int((e - s) // tf_ms(tf)), tf, 60 + k, t0=s)
        for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
            rows = [[int(t) * (1000 if t >= G.day_ms("2025-01-01") else 1), o, h, lo, c, v, (int(t) + tf_ms(tf) - 1), 0, 1, 0, 0, 0]
                    for t, o, h, lo, c, v in g[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False)]
            files[G.spot_url("PAXGUSDT", tf, mon)] = kline_zip(rows)
    for j, (sym, (a, b)) in enumerate(windows["venue"].items()):
        s2, e2 = G.window_ms(a, b)
        for k, tf in enumerate(("15m", "1h", "4h")):
            df = synth(int((e2 - s2) // tf_ms(tf)), tf, 80 + 10 * j + k, t0=s2)
            for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
                rows = [[int(t), o, h, lo, c, v, int(t) + tf_ms(tf) - 1, 0, 1, 0, 0, 0]
                        for t, o, h, lo, c, v in g[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False)]
                files[f"{L.ARCHIVE_BASE}/monthly/klines/{sym}/{tf}/{sym}-{tf}-{mon}.zip"] = kline_zip(rows, header=True)
        for mon in sorted({pd.Timestamp(t, unit="ms", tz="UTC").strftime("%Y-%m") for t in range(s2, e2, DAY)}):
            m0 = G.day_ms(mon + "-01")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(
                    f"{m0 + k * 8 * HOUR},8,0.0001" for k in range(31 * 3) if m0 + k * 8 * HOUR < L._next_month(m0)))
            files[f"{L.ARCHIVE_BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mon}.zip"] = buf.getvalue()
    return files


def test_end_to_end_offline_no_network_and_cli(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ağ kullanılmamalı")
    monkeypatch.setattr(L, "_http_get", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    windows = {"main": ("2024-01-01", "2024-03-31"), "venue": {"XAUUSDT": ("2024-02-01", "2024-03-31"),
                                                              "PAXGUSDT": ("2024-01-01", "2024-03-31")}}
    files = _fake_archive(windows)
    asked = []
    fetch = lambda url: (asked.append(url), files.get(url))[1]  # noqa: E731
    dw = {"main": {"start": windows["main"][0], "end": windows["main"][1]},
          "venue": {"symbols": {k: list(v) for k, v in windows["venue"].items()}},
          "dukascopy": {"start": "2024-01-01", "end": "2024-01-07"}}
    now = G.day_ms("2024-06-01")
    cfg = L.LabConfig(bootstrap_iters=200)
    out = tmp_path / "out"
    rep = G.run(sections=["main", "venue", "dukascopy"], cache_dir=tmp_path / "cache", out_dir=out, cfg=cfg, fetch=fetch,
                now_ms=now, log=lambda s: None, data_windows=dw, duka_root=tmp_path / "duka")
    assert rep["registry_sha"] == PINNED_SHA and rep["sections"] == ["main", "venue", "dukascopy"]
    assert len(rep["main"]["cells"]) == 32 and rep["dukascopy"]["status"] == "yapılamadı"
    assert all(c["IS"]["n"] + c["OOS"]["n"] == c["trades"] for c in rep["main"]["cells"])
    assert any(c["verdict"] == L.V_THIN and c["trades"] for c in rep["main"]["cells"])
    assert rep["dukascopy"]["coverage"]["coverage"] == 0.0 and rep["data"]["spot PAXGUSDT 1h"]["bars"] == 91 * 24
    assert rep["data"]["spot PAXGUSDT 15m"]["missing_ratio"] == 0.0 and rep["data"]["futures XAUUSDT 4h"]["bars"] == 60 * 6
    rows = rep["venue"]["rows"]
    assert {r["symbol"] for r in rows} == {"XAUUSDT", "PAXGUSDT"} and len(rows) == 64
    assert any((r["funding_r"] or {}).get("known") for r in rows)
    js = json.loads((out / "gold_lab_report.json").read_text(encoding="utf-8"))
    md = (out / "gold_lab_report.md").read_text(encoding="utf-8")
    assert js["registry_sha"] == PINNED_SHA and PINNED_SHA in md and "yapılamadı" in md.lower()
    with gzip.open(out / "gold_lab_events.csv.gz", "rt", encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
        n_rows = sum(1 for _ in fh)
    assert head == G.EVENT_COLS and n_rows > 100
    n_asked = len(asked)
    rep2 = G.run(sections=["main"], cache_dir=tmp_path / "cache", out_dir=tmp_path / "out2", cfg=cfg, offline=True, now_ms=now,
                 log=lambda s: None, data_windows=dw)
    assert len(asked) == n_asked and rep2["archive_requests"] == 0                           # çevrimdışı: yalnız önbellek
    assert [c["OOS"] for c in rep2["main"]["cells"]] == [c["OOS"] for c in rep["main"]["cells"]]
    # komut satırı: ön kayıtlı pencere önbellekte yok → çevrimdışı HATA (ağ YOK), bilinmeyen bölüm HATA
    spec = importlib.util.spec_from_file_location("gold_cli", ROOT / "scripts" / "gold_lab.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["--section", "main", "--offline", "--cache", str(tmp_path / "c2"), "--out", str(tmp_path / "o2")]) == 2
    assert cli.main(["--section", "nope", "--cache", str(tmp_path / "c2"), "--out", str(tmp_path / "o2")]) == 2


def test_existing_section_runs_the_unchanged_lab_sets_on_the_documented_timeframes(monkeypatch):
    calls = []

    def fake(df, symbol, tf, cfg, *, catalog=True, algos=True, extras=True, variations=(), **kw):
        calls.append((tf, catalog, extras, algos, tuple(variations), cfg))
        return [], {"symbol": symbol, "tf": tf, "bars": len(df), "signals": 0, "trades": 0, "skipped": {}}
    monkeypatch.setattr(L, "process_series", fake)
    ids = tuple(G.variation_ids())
    metas = []
    for tf in ("15m", "1h", "4h", "1d"):
        metas += G._existing_task((synth(400, tf, 7), "PAXGUSDT", tf, dataclasses.asdict(L.LabConfig())))[1]
    # katalog/ek/algoritma tek koşu; mum varyasyonları AYRI yalnız-varyasyon koşusu (signal_lab CLI'nin kuralı)
    assert [c[:5] for c in calls] == [("15m", False, True, False, ()), ("1h", True, True, True, ()), ("1h", False, False, False, ids),
                                      ("4h", True, True, True, ()), ("4h", False, False, False, ids), ("1d", False, False, True, ())]
    assert ids and all(c[5] == L.LabConfig() for c in calls) and [m["set"] for m in metas] == ["lab", "lab", "variations", "lab",
                                                                                               "variations", "lab"]
    rep = G.existing_report({"lab": [], "variations": []}, metas, L.LabConfig())
    assert "KEŞİF" in rep["label_tr"] and set(rep["sets"]) == {"lab", "variations"} and rep["sets"]["lab"]["tested"] == 0
    assert len(rep["sets"]["variations"]["series"]) == 2 and "XSMOM" in rep["xsmom_tr"]
    short = G._existing_task((synth(50, "1d", 8), "PAXGUSDT", "1d", dataclasses.asdict(L.LabConfig())))
    assert short == ({"lab": [], "variations": []}, [{"symbol": "PAXGUSDT", "tf": "1d", "bars": 50, "error": "YETERSİZ_VERİ", "set": "lab"}])


def test_lab_modules_unchanged_by_gold_lab_import():
    import tradingbot.candle_lab  # noqa: F401
    src = (ROOT / "tradingbot" / "signal_lab.py").read_text(encoding="utf-8")
    assert "gold_lab" not in src, "signal_lab altın laboratuvarını bilmez (bağımlılık tek yönlü)"
    assert L.LabConfig() == L.LabConfig(fee_pct=0.05, slippage_bps=3.0, max_hold_bars=24, default_rr=2.0)
