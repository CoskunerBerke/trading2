# -*- coding: utf-8 -*-
"""Defter araştırması book_v1 (docs/BOOK_RESEARCH_V1.md; 2026-10-05): ön kayıt mührü ve kaydın belgeyi kapsaması (okunuşlar
belgede), sabit pencereler ve dönem sınırları, eşdeğerlik (D4 = donchian_trend + algo_events; C4 = candle_lab.variation_events
+ candle_book.decide tekilleştirmesi; Formasyon = catalog_events adım 1 + RSI = build_plans_v3; rejim = regime_gate.btc_regime;
temiz pencere = candle_lab.valid_ends), GELECEĞE BAKMAMA (her varyantın sinyali ve işlemi, kesilmiş seri ile geleceği
değiştirilmiş tam seride aynı), simülatörün signal_lab.simulate / simulate_rule paritesi ve DATA_END/DELISTED örnekleri,
maliyet, plasebo (belirlenimci, anahtar, dönem oranı, q havuzu yalnız önceki isabetlerden), C4 tekilleştirme örnekleri,
fonlama (funding_carry + NaN doldurma), aylık ölçü (işlemsiz ay, çıkış ayı, aralık), kapasite (elle hesaplanmış BUMP/NO_BUMP,
slot, aynı sembol, çıkışla boşalan slot, sıra, TOTAL_RISK), hedef şartları, Holm, §8.5 basamakları ve aday oranı, sansür /
BAD_BAR payı, plasebo karşılaştırma eşiği, Formasyon SHORT ve D4_08 kuralları, PIT (havuz, sıralama, liste, eksik çift),
yalnız aylık fonlama dosyaları ve PRICE_END sonrası uzlaşma, veri hatasında koşunun durması, kod durumu (aynı kod olmadan
birleştirme yok), belirlenimci işlem dosyası, uçtan uca kuru koşu (sahte arşiv, ağ YOK) ve komut satırı. Yalnız sentetik veri."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import io
import json
import math
import urllib.request
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import book_lab as B
from tradingbot import signal_lab as L
from tradingbot.timeframes import tf_ms

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000
HOUR = 3_600_000
H4 = 4 * HOUR

#: ön kayıt mührü — bir varyant tanımı, süzgeç, plasebo, istatistik/kapasite/aylık ayarı, veri penceresi, evren, PIT kuralı
#: ya da okunuş değişirse bu test KIRILIR (book_v2 + belge + yeni deneme sayısı; sonuç görüldükten sonra gevşetme YOK)
PINNED_SHA = "2a3cecc16e0a85c1"
#: testlerde sabit kod durumu (temiz ağaç); gerçek koşuda `git_state`
CODE = {"commit": "c0ffee", "code_tree": "t1", "dirty": False}


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


# ---------------------------------------------------------------------------- sentetik veri
def synth(n, tf, t0, seed=1, vol=0.01, drift=0.0, sub=8, spike=0.08, legs=0, leg_drift=0.006):
    """Mum içi yollu rastgele yürüyüş; `legs` > 0 ise sürüklenme her `legs` barda yön değiştirir (trend bacakları)."""
    rnd = np.random.default_rng(seed)
    step = tf_ms(tf)
    dr = np.full(n, drift, dtype=float)
    if legs:
        dr = dr + leg_drift * np.where((np.arange(n) // legs) % 2 == 0, 1.0, -1.0)
    steps = rnd.normal(0, vol / np.sqrt(sub), (n, sub)) + (dr / sub)[:, None]
    path = 100.0 * np.exp(np.cumsum(steps.ravel())).reshape(n, sub)
    o = np.r_[100.0, path[:-1, -1]]
    ts = t0 + np.arange(n, dtype=np.int64) * step
    v = rnd.uniform(50, 150, n)
    v[rnd.random(n) < spike] *= 3.0
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, path.max(1)), "low": np.minimum(o, path.min(1)),
                         "close": path[:, -1], "volume": v, "close_time": ts + step - 1})


def wave_daily(n, t0, period=70, seed=9):
    """BTC 1d: EMA200'ü sık kesen dalga (iki rejim de görülsün)."""
    rnd = np.random.default_rng(seed)
    k = np.arange(n)
    c = 100.0 * np.exp(0.25 * np.sin(2 * np.pi * k / period) + 0.01 * rnd.normal(size=n).cumsum() * 0.1)
    o = np.r_[c[0], c[:-1]]
    ts = t0 + k.astype(np.int64) * DAY
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c,
                         "volume": rnd.uniform(50, 150, n), "close_time": ts + DAY - 1})


def flat_series(n, tf="4h", t0=None, px=100.0, rng=1.0):
    t0 = B.DECISION_START_MS if t0 is None else t0
    step = tf_ms(tf)
    ts = t0 + np.arange(n, dtype=np.int64) * step
    return pd.DataFrame({"timestamp": ts, "open": px, "high": px + rng, "low": px - rng, "close": px, "volume": 100.0,
                         "close_time": ts + step - 1})


# ---------------------------------------------------------------------------- küçük dünya (zaman sabitleri yamalı)
T0 = B.START_4H_MS                         # 2022-01-01
MINI = {"START_4H_MS": T0, "START_1D_MS": T0 - 400 * DAY, "DECISION_START_MS": ms("2022-04-01"), "IS_END_MS": ms("2022-05-01"),
        "PRICE_END_MS": ms("2022-07-01"), "FUNDING_START_MS": ms("2022-03-01"), "FUNDING_END_MS": ms("2022-07-02"),
        "IS_MONTHS": ["2022-04"], "OOS_MONTHS": ["2022-05", "2022-06"], "PIT_MONTHS": ["2022-04", "2022-05", "2022-06"]}
N4 = (MINI["PRICE_END_MS"] - T0) // H4     # 1086 bar
N1 = (MINI["PRICE_END_MS"] - MINI["START_1D_MS"]) // DAY


def patch_mini(mp) -> None:
    for k, v in MINI.items():
        mp.setattr(B, k, v)


@pytest.fixture
def mini(monkeypatch):
    patch_mini(monkeypatch)
    return MINI


def world_frames(seed=11):
    df4 = synth(N4, "4h", T0, seed=seed, vol=0.012, legs=45, leg_drift=0.004)
    df1 = synth(N1, "1d", MINI["START_1D_MS"], seed=seed + 1, vol=0.03, legs=40, leg_drift=0.01)
    btc1 = wave_daily(N1, MINI["START_1D_MS"])
    return df4, df1, btc1


def tcut(df, t):
    return df[df["timestamp"] + (df["timestamp"].diff().median() if len(df) > 1 else 0) <= t].reset_index(drop=True)


@pytest.fixture(scope="module")
def world():
    """Küçük dünyada bir sembolün bütün defter işlemleri: tam seri (geleceği DEĞİŞTİRİLMİŞ) ve kesim anında kesilmiş seri."""
    with pytest.MonkeyPatch.context() as mp:
        patch_mini(mp)
        df4, df1, btc1 = world_frames()
        cut = 930
        t_cut = int(df4["timestamp"].iloc[cut])
        fut4 = df4.copy()
        fut4.loc[cut:, ["open", "high", "low", "close"]] *= 1.7                    # gelecek tamamen farklı
        fut1, futb = df1.copy(), btc1.copy()
        m1 = fut1["timestamp"] + DAY > t_cut
        fut1.loc[m1, ["open", "high", "low", "close"]] *= 0.6
        futb.loc[futb["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 1.9
        full, fmeta = B.process_symbol("SYN/USDT", fut4, fut1, B.regime_daily(futb), None, B.BOOKS)
        trunc, tmeta = B.process_symbol("SYN/USDT", df4.iloc[:cut].reset_index(drop=True), tcut(df1, t_cut),
                                        B.regime_daily(tcut(btc1, t_cut)), None, B.BOOKS)
        base, bmeta = B.process_symbol("SYN/USDT", df4, df1, B.regime_daily(btc1), None, B.BOOKS)
    return {"full": full, "trunc": trunc, "base": base, "t_cut": t_cut, "fmeta": fmeta, "bmeta": bmeta, "df4": df4, "df1": df1,
            "btc1": btc1}


# ---------------------------------------------------------------------------- mühür ve kayıt
def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def test_registry_sha_is_pinned_documented_and_sensitive_to_every_part():
    assert B.BOOK_REGISTRY_SHA == _sha(B.BOOK_REGISTRY) == PINNED_SHA, "kayıt değişti: book_v2 + belge + yeni deneme sayısı"
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    assert f"Ön kayıt mührü: `BOOK_REGISTRY_SHA = {PINNED_SHA}`" in doc and B.VERSION == "book_v1"
    assert "henüz yok" not in doc.split("## 0.")[0]
    R = B.BOOK_REGISTRY
    for path, val in ((("variants", "D4_05_STOP3ATR", "stop_atr"), 2.5), (("variants", "D4_00_BASE", "risk_atr"), [0.1, 5.0]),
                      (("variants", "C4_02_EXIT_1R", "dsl_change", "exit", "target_r"), 1.5), (("variants", "FM_00_BASE", "chase_atr"), 2.0),
                      (("variants", "FM_08_EXIT_3R", "exit", "max_bars"), 48), (("filters", "COST_STOP"), "x"),
                      (("stats", "min_oos"), 19), (("monthly", "seed"), 1), (("capacity", "books", "FM", "slots"), 3),
                      (("data", "price_end_ms"), 1), (("universes", "primary"), []), (("readings_tr",), []),
                      (("universes", "pit", "top"), 50), (("multiple", "iters"), 1000), (("target", "censor_max"), 0.2),
                      (("c4", "placebo_p"), 0.2), (("placebo", "min_n"), 10)):
        tweak = json.loads(json.dumps(R))
        node = tweak
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        assert _sha(tweak) != PINNED_SHA, path


def test_registry_covers_the_document_counts_and_readings():
    R = B.BOOK_REGISTRY
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    hyp = [v for v, d in B.VARIANTS.items() if not d["control"]]
    assert len(hyp) == 32 and [v for v, d in B.VARIANTS.items() if d["control"]] == ["FM_CTRL_NO_RSI"]
    assert all(f"`{v}`" in doc for v in B.VARIANTS)
    assert "FM_06" not in "".join(B.VARIANTS)                                       # çıkarıldı (§6.3)
    by_book = {b: sum(1 for c in B.PRIMARY_CELLS if B.VARIANTS[c.split("|")[0]]["book"] == b) for b in B.BOOKS}
    assert by_book == {"D4": 15, "C4": 27, "FM": 12} and len(B.PRIMARY_CELLS) == 54 == R["trials"]["primary_cells"]
    assert {b: sum(1 for v in hyp if B.VARIANTS[v]["book"] == b) for b in B.BOOKS} == {"D4": 13, "C4": 9, "FM": 10}
    for v, d in B.VARIANTS.items():                                                 # plasebo süzgeci = süzgeç − sinyal süzgeci
        assert d["placebo_filters"] == [f for f in d["filters"] if f not in B.SIGNAL_FILTERS], v
        assert d["cells"] == (["BOTH", "LONG", "SHORT"] if len(d["sides"]) == 2 else ["LONG"]), v
    # okunuşların HEPSİ belgede (harfiyen)
    for r in B.READINGS_TR:
        assert r in doc, r[:60]
    assert R["readings_tr"] == list(B.READINGS_TR)
    for s in ("BTCDOM", "XAU", "TSLA", "USDC"):
        assert s in B.READINGS_TR[-1]


def test_c4_definition_shas_match_doc_and_registry_and_only_changed_definitions_get_new_shas():
    from tradingbot import candle_variations as CV
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    for cv, sha in B.CV_BASE_SHA.items():
        assert CV.get(cv).definition_sha == sha and f"`{sha}`" in doc
    shas = B.BOOK_REGISTRY["c4"]["definition_sha"]
    for vid, d in B.C4_VARIANTS.items():
        for cv in d["cvs"]:
            same = d["dsl_change"] is None
            assert (shas[vid][cv] == B.CV_BASE_SHA[cv]) is same, (vid, cv)
            var = B.c4_variation(cv, d["dsl_change"])
            assert var.id == cv and var.side == CV.get(cv).side
    v2, v3 = B.c4_variation(B.CV_ORDER[0], B.C4_VARIANTS["C4_02_EXIT_1R"]["dsl_change"]), \
        B.c4_variation(B.CV_ORDER[0], B.C4_VARIANTS["C4_03_EXIT_3R"]["dsl_change"])
    assert (v2.target_r, v2.max_hold_bars, v3.target_r, v3.max_hold_bars) == (1.0, 24, 3.0, 60)
    assert B.c4_variation(B.CV_ORDER[0], {"stop": {"atr_buffer": 1.0}}).atr_buffer == 1.0
    assert B.c4_variation(B.CV_ORDER[0], {"context": {"atr_regime": [None, 1.25]}}).context == {"trend": ["with"], "atr_regime": [None, 1.25]}
    # defter kaydı değişmedi
    assert len(CV.VARIATIONS) == 9 and CV.get(B.CV_ORDER[0]).definition_sha == B.CV_BASE_SHA[B.CV_ORDER[0]]


def test_constants_windows_universes_and_capacity_match_the_bot():
    from tradingbot import learning_mode as LM
    from tradingbot import strategy_paper as SP
    from tradingbot.pattern_trader.strategy_v3 import V3_SYMBOLS
    from tradingbot.risk.profiles import PROFILES
    assert B.START_4H_MS == ms("2022-01-01") and B.START_1D_MS == ms("2020-01-01") and B.PRICE_END_MS == ms("2026-10-01")
    assert B.DECISION_START_MS == ms("2023-01-01") and B.IS_END_MS == ms("2025-01-01")
    assert B.FUNDING_START_MS == ms("2022-12-01") and B.FUNDING_END_MS == ms("2026-10-02")
    assert len(B.PRIMARY_UNIVERSE) == 23 and set(B.PRIMARY_UNIVERSE) == set(V3_SYMBOLS) & set(B.INFO_UNIVERSE)
    assert len(B.INFO_UNIVERSE) == 40 == len(set(B.INFO_UNIVERSE))
    assert B.CAP_COMMON["hard_cap_pct"] == LM.HARD_CAP_PCT and B.CAP_COMMON["mmr"] == SP.LEARNING_MMR
    assert B.CAP_COMMON["max_position_pct"] == PROFILES["PAPER_RESEARCH"].max_position_pct
    cfg = L.LabConfig()
    assert (cfg.fee_pct, cfg.slippage_bps) == (0.05, 3.0) and round(2 * cfg.cost_per_side * 100, 4) == 0.16


def test_split_dates_and_month_lists():
    assert B.period_of(ms("2022-12-31") + 20 * HOUR) is None                       # ısınma
    assert B.period_of(ms("2023-01-01")) == "IS" and B.period_of(ms("2025-01-01") - 1) == "IS"
    assert B.period_of(ms("2025-01-01")) == "OOS" and B.period_of(ms("2026-10-01") - 1) == "OOS"
    assert B.period_of(ms("2026-10-01")) is None
    # 2024-12-31 20:00'de açılan 4h barın kararı 2025-01-01 00:00'dadır → doğrulama
    assert B.period_of(ms("2024-12-31") + 20 * HOUR + H4) == "OOS"
    assert len(B.IS_MONTHS) == 24 and B.IS_MONTHS[0] == "2023-01" and B.IS_MONTHS[-1] == "2024-12"
    assert len(B.OOS_MONTHS) == 21 and B.OOS_MONTHS[0] == "2025-01" and B.OOS_MONTHS[-1] == "2026-09"
    assert B.month_key(ms("2026-10-01") - 1) == "2026-09"


# ---------------------------------------------------------------------------- yeniden yazılan parçaların eşdeğerliği
def test_clean_ends_equals_candle_lab_valid_ends_and_regime_equals_btc_regime():
    from tradingbot import candle_lab as CL
    from tradingbot import regime_gate as RG
    df = synth(1400, "4h", T0, seed=5)
    df.loc[700, "high"] = df.loc[700, "close"] - 0.5                                # bozuk bar
    df.loc[900, "volume"] = -1.0
    df = df.drop(index=[1100, 1101]).reset_index(drop=True)                         # boşluk
    a = CL._arrays(df)
    got = B.clean_ends(a["ts"], a["o"], a["h"], a["l"], a["c"], a["v"], H4, 500)
    assert np.array_equal(got, CL.valid_ends(a, H4)) and got.any() and not got.all()
    closes = list(wave_daily(650, ms("2020-01-01"))["close"])
    closes[300] = float("nan")
    want = [RG.btc_regime([{"close": c} for c in closes[:k + 1]]) for k in range(len(closes))]
    assert B.regime_values(closes) == want and want.count("UP") and want.count("DOWN") and want[:199] == [None] * 199
    # rejim haritası: open_time + 1 gün ≤ t olan son bar
    d = wave_daily(700, ms("2021-01-01"))
    d_ts, reg = B.regime_daily(d)
    k = 350
    t = int(d_ts[k]) + DAY
    assert B.regime_at(np.array([t, t - 1]), d_ts, reg).tolist() == [reg[k], reg[k - 1]]
    assert reg[:199].tolist() == [0] * 199
    # bayat değer kullanılmaz: kullanılan bar t'den önceki son 24 saatte kapanmadıysa bilinmiyor
    assert B.regime_at(np.array([t + DAY - 1, t + DAY + 1]), d_ts[:k + 1], reg[:k + 1]).tolist() == [reg[k], 0]
    d2 = d.drop(index=[300]).reset_index(drop=True)                                   # 1d boşluk → 200 bar temiz değil
    _, reg2 = B.regime_daily(d2)
    assert reg2[300:499].tolist() == [0] * 199 and reg2[499] != 0


def test_channels_and_context_masks_are_the_lab_constructions():
    df = synth(400, "4h", T0, seed=6)
    S = B.Series(df, "4h")
    aux = L.aux_series(df)
    for name in ("hi20", "lo20", "hi10", "lo10"):
        assert np.array_equal(S.chan(name), aux[name], equal_nan=True)
        assert np.array_equal(B.channel(df["high" if name[:2] == "hi" else "low"].to_numpy(), int(name[2:]), name[:2]), aux[name],
                              equal_nan=True)
    h55 = S.chan("hi55")
    assert np.isnan(h55[54]) and h55[55] == df["high"].iloc[:55].max()
    vok, vcf = S.vol_masks()
    for i in (250, 300, 399):
        cx = L.context(S.ind, S.arr, i, B.LONG)
        assert vok[i] == (cx["volatilite"] in ("düşük", "normal")) and vcf[i] == (cx["hacim"] == "yüksek(>1.5x)")


# ---------------------------------------------------------------------------- taban varyantların canlı kuralla eşdeğerliği
def test_d4_base_equals_the_live_book_rule_and_the_lab_algorithm():
    from tradingbot import donchian_trend as DT
    df = synth(700, "4h", ms("2024-01-01"), seed=7, legs=40, leg_drift=0.005)
    S = B.Series(df, "4h")
    masks = B._masks(S, None, None)
    raw, elig, unclean = B.d4_raw(S, B.D4_VARIANTS["D4_00_BASE"], masks, B.LONG)
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    live = {}
    for i in range(B.D4_MIN_I, len(df)):
        act = DT.decide(rows=rows[:i + 1])
        if act and act["action"] == "OPEN":
            live[i] = act["stop"]
    assert len(raw) >= 5 and set(raw) == set(live) and unclean == 0
    a = S.ind["atr"]
    for i in raw:
        assert math.isclose(S.c[i] - 2.0 * a[i], live[i], rel_tol=1e-12)
    lab = {e.i: e for e in L.algo_events(df, "X/USDT", "4h", a, S.aux) if e.name == "TREND_DONCHIAN_20_10" and e.side == B.LONG}
    assert set(lab) == set(raw)
    for i, e in lab.items():
        assert e.stop == S.c[i] - 2.0 * a[i] and e.exit == {"kind": "channel", "max_bars": 300}
    assert B.D4_VARIANTS["D4_00_BASE"]["exit"] == {"kind": "channel", "ref": {"LONG": "lo10", "SHORT": "hi10"}, "max_bars": 300}
    assert (DT.ENTRY_N, DT.EXIT_N, DT.STOP_ATR, DT.MAX_BARS, DT.MIN_RISK_ATR, DT.MAX_RISK_ATR) == (20, 10, 2.0, 300, 0.1, 10.0)
    # SHORT (D4_10) = laboratuvarın SHORT kolu
    sraw, _, _ = B.d4_raw(S, {**B.D4_VARIANTS["D4_10_1D_REGIME_BOTH"], "tf": "4h", "filters": []}, masks, B.SHORT)
    lab_s = {e.i for e in L.algo_events(df, "X/USDT", "4h", a, S.aux) if e.name == "TREND_DONCHIAN_20_10" and e.side == B.SHORT}
    assert set(sraw) == lab_s and len(lab_s) >= 3


def test_c4_base_equals_variation_events_and_the_live_book_dedup():
    from tradingbot import candle_book as CB
    from tradingbot import candle_lab as CL
    df = synth(900, "4h", ms("2024-01-01"), seed=21, vol=0.015, legs=30, leg_drift=0.005, spike=0.15)
    S = B.Series(df, "4h")
    det = B.c4_detect(S, "X/USDT", ["base"])
    real = {}
    for cv in B.CV_ORDER:
        evs, atr_sig, _ = det["base"][cv]
        want, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, None))
        assert [(e.name, e.side, e.i, e.stop, e.family) for e in evs] == [(e.name, e.side, e.i, e.stop, e.family) for e in want]
        real[cv] = {e.i: e for e in evs if e.family == CL.FAMILY}
    win, sup = B.c4_dedup(real, B.CV_ORDER, "BOTH")
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    params = CB.CandleParams(variations=tuple(B.CV_ORDER))
    live = {}
    for i in range(499, len(df)):
        act = CB.decide("c4_candle_variations", rows=rows[i - 499:i + 1], params=params)
        if act and act["action"] == "OPEN":
            live[i] = (act["lab_algo"], act["stop"], tuple(act["also_matched"]))
    assert len(win) >= 5 and {i: cv for cv, i in win} == {i: v[0] for i, v in live.items()}
    for cv, i in win:
        assert live[i][1] == real[cv][i].stop
    assert sup == sum(len(v[2]) for v in live.values())
    longs, _ = B.c4_dedup(real, B.CV_ORDER, "LONG")
    assert all(B.c4_variation(cv, None).side == "LONG" for cv, _ in longs)


def test_c4_exit_only_changes_share_the_base_detection():
    """C4_02 / C4_03 tabanın tespitini paylaşır: detect_last ve placebo_context çıkışı OKUMAZ."""
    from tradingbot import candle_lab as CL
    df = synth(700, "4h", ms("2024-01-01"), seed=22, vol=0.015, legs=30, leg_drift=0.005, spike=0.15)
    for vid in ("C4_02_EXIT_1R", "C4_03_EXIT_3R"):
        assert B.C4_VARIANTS[vid]["detection"] == "base" and set(B.C4_VARIANTS[vid]["dsl_change"]) == {"exit"}
        for cv in B.CV_ORDER[:4]:
            a, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, None))
            b, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, B.C4_VARIANTS[vid]["dsl_change"]))
            assert [(e.name, e.i, e.stop, e.family) for e in a] == [(e.name, e.i, e.stop, e.family) for e in b]


def test_formasyon_base_equals_catalog_step1_plus_rsi_and_the_live_v3_plans():
    from tradingbot.pattern_trader.strategy_v3 import build_plans_v3
    from tradingbot.structures.analysis import analyze
    df = synth(560, "4h", ms("2024-01-01"), seed=31, vol=0.01, legs=25, leg_drift=0.012)
    S = B.Series(df, "4h")
    recs = B.fm_records(S, "PAR/USDT")
    hits, _, unclean = B.fm_raw(S, recs, B.FM_VARIANTS["FM_00_BASE"], B._masks(S, None, None), B.LONG)
    cfg1 = dataclasses.replace(L.LabConfig(), stride=1)
    lab = [e for e in L.catalog_events(df, "PAR/USDT", "4h", cfg1) if e.name == "THREE_WHITE_SOLDIERS" and e.side == "LONG"
           and S.ind["rsi"][e.i] > 70]
    assert [(e.i, e.stop, e.trigger) for e in hits] == [(e.i, e.stop, e.trigger) for e in lab] and len(hits) >= 3 and unclean == 0
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    bot = {}
    for end in range(300, len(rows) + 1):
        as_of = int(rows[end - 1]["timestamp"]) + H4
        an = analyze(market="USDM_PERP", symbol="PAR/USDT", timeframe="4h", bars=rows[end - 300:end], as_of_ms=as_of)
        plans, _ = build_plans_v3("PAR/USDT", as_of_ms=as_of, analyses={"4h": an}, bars_by_tf={"4h": rows[:end]})
        for p in plans:
            bot[end - 1] = (p["stop"], p["trigger"]["level"])
    assert {e.i: (round(e.stop, 10), round(e.trigger, 10)) for e in hits} == bot
    from tradingbot.pattern_trader.strategy_v3 import V3_SIGNAL
    sp = B.FM_VARIANTS["FM_00_BASE"]
    assert sp["exit"] == {"kind": "target", "rr": V3_SIGNAL["target_rr"], "max_bars": V3_SIGNAL["hold_bars"]}
    assert sp["risk_atr"] == [V3_SIGNAL["min_risk_atr"], V3_SIGNAL["max_risk_atr"]] and sp["chase_atr"] == V3_SIGNAL["chase_atr"]
    assert sp["signals"]["LONG"] == {"name": V3_SIGNAL["name"], "rsi_op": ">", "rsi": V3_SIGNAL["rsi_min"]} and V3_SIGNAL["side"] == "LONG"


# ---------------------------------------------------------------------------- simülatör
def _arr(df):
    return {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}


def test_simulator_parity_with_lab_simulate_and_simulate_rule():
    df = synth(900, "4h", ms("2024-01-01"), seed=41, vol=0.015)
    S = B.Series(df, "4h")
    arr, atr, aux = _arr(df), S.ind["atr"], S.aux
    cfg = L.LabConfig()
    rnd = np.random.default_rng(3)
    n_fix = n_rule = 0
    for _ in range(600):
        i = int(rnd.integers(20, len(df) - 1))
        side = B.LONG if rnd.random() < 0.5 else B.SHORT
        s = 1 if side == B.LONG else -1
        a = float(atr[i])
        stop = float(S.c[i]) - s * float(rnd.uniform(0.0, 6.0)) * a
        # sabit hedef
        rr, H = float(rnd.choice([1.0, 2.0, 3.0])), int(rnd.choice([12, 24, 60]))
        trig = float(S.c[i]) if rnd.random() < 0.5 else None
        ev = L.Event("X", "4h", "t", "T", side, i, int(S.ts[i]) + H4, stop, trigger=trig)
        vc = dataclasses.replace(cfg, default_rr=rr, max_hold_bars=H)
        lab = L.simulate(ev, arr, atr, vc)
        tr, why = B.simulate_trade(S, i, side, stop, {"kind": "target", "rr": rr, "max_bars": H}, (0.1, 5.0), a, cfg, trigger=trig,
                                   chase_atr=1.0 if trig is not None else None)
        if lab == "":
            n_fix += 1
            assert why == "" and tr["reason"] == ev.exit_reason and tr["hold"] == ev.hold and math.isclose(tr["r"], ev.r, rel_tol=1e-12)
            assert math.isclose(tr["cost_r"], ev.cost_r, rel_tol=1e-12)
        elif lab != "NO_FUTURE_DATA":
            assert why == lab
        else:                                                                         # j + H > n: süre sınırına varılamaz
            assert why != "" or tr["reason"] in ("STOP", "TARGET", "DATA_END", "DELISTED")
        # kanal çıkışı
        ev2 = L.Event("X", "4h", "a", "T", side, i, int(S.ts[i]) + H4, stop, exit={"kind": "channel", "max_bars": H})
        lab2 = L.simulate_rule(ev2, arr, atr, aux, cfg)
        tr2, why2 = B.simulate_trade(S, i, side, stop, {"kind": "channel", "ref": {"LONG": "lo10", "SHORT": "hi10"}, "max_bars": H},
                                     (0.1, 10.0), a, cfg)
        if lab2 == "":
            n_rule += 1
            assert why2 == "" and tr2["reason"] == ev2.exit_reason and tr2["hold"] == ev2.hold
            assert math.isclose(tr2["r"], ev2.r, rel_tol=1e-12)
        elif lab2 != "NO_FUTURE_DATA":
            assert why2 == lab2
    assert n_fix > 150 and n_rule > 150


def test_data_end_delisted_cost_and_exit_times(mini):
    cfg = L.LabConfig()
    df = flat_series(N4, t0=T0)
    S = B.Series(df, "4h")
    n = S.n
    # sabit hedef, son barlarda açılan işlem: veri sonunda DATA_END, son barın kapanışı, maliyet dahil
    tr, why = B.simulate_trade(S, n - 5, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert why == "" and tr["reason"] == "DATA_END" and tr["exit_px"] == 100.0 and tr["xb"] == n - 1
    assert tr["exit_ms"] == B.PRICE_END_MS and tr["hold"] == 4
    cost = 200.0 * cfg.cost_per_side
    assert math.isclose(tr["r"], -cost / 2.0, rel_tol=1e-12) and math.isclose(tr["cost_r"], cost / 2.0)
    # süre sınırı (TIME): son bar kapanışı, maliyet yine dahil
    tr, _ = B.simulate_trade(S, 100, B.SHORT, 102.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["reason"] == "TIME" and tr["xb"] == 124 and tr["exit_ms"] == int(S.ts[124]) + H4 and tr["r"] < 0
    # kanal: son barda kural tetiklenirse de DATA_END
    df2 = df.copy()
    df2.loc[n - 1, ["close", "low"]] = [99.5, 98.9]
    S2 = B.Series(df2, "4h")
    tr, _ = B.simulate_trade(S2, n - 3, B.LONG, 90.0, B.D4_VARIANTS["D4_00_BASE"]["exit"], (0.1, 10.0), 1.0, cfg)
    assert tr["reason"] == "DATA_END" and tr["exit_px"] == 99.5 and tr["xb"] == n - 1
    # kural çıkışı: k+1 açılışı, çıkış anı o açılış
    df3 = df.copy()
    df3.loc[300, ["close", "low"]] = [98.5, 98.4]
    S3 = B.Series(df3, "4h")
    tr, _ = B.simulate_trade(S3, 290, B.LONG, 90.0, B.D4_VARIANTS["D4_00_BASE"]["exit"], (0.1, 10.0), 1.0, cfg)
    assert tr["reason"] == "RULE" and tr["xb"] == 301 and tr["exit_ms"] == int(S3.ts[301]) and tr["exit_bar_ms"] == int(S3.ts[301])
    # STOP: isabet barının kapanışı
    df4 = df.copy()
    df4.loc[305, "low"] = 97.0
    tr, _ = B.simulate_trade(B.Series(df4, "4h"), 300, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0),
                             1.0, cfg)
    assert tr["reason"] == "STOP" and tr["exit_ms"] == int(S.ts[305]) + H4
    # seri erken biterse DELISTED; giriş barı yoksa NO_ENTRY_BAR
    S5 = B.Series(df.iloc[:500].reset_index(drop=True), "4h")
    tr, _ = B.simulate_trade(S5, 495, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["reason"] == "DELISTED" and tr["exit_ms"] == int(S5.ts[499]) + H4
    assert B.simulate_trade(S5, 499, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)[1] == "NO_ENTRY_BAR"
    # COST_STOP: s < %1,6 → ret; eşitlik geçer
    S125 = B.Series(flat_series(N4, t0=T0, px=125.0), "4h")
    assert B.simulate_trade(S125, 100, B.LONG, 123.01, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg,
                            cost_stop=True)[1] == "STOP_TOO_TIGHT_FOR_COST"
    assert B.simulate_trade(S125, 100, B.LONG, 123.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg,
                            cost_stop=True)[1] == ""                                  # 2 / 125 = 0,016
    # bozuk bar geçen işlem BAD_BAR; boşluk geçen işlem işaretlenir
    df6 = df.copy()
    df6.loc[110, "high"] = 50.0
    assert B.simulate_trade(B.Series(df6, "4h"), 100, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0),
                            1.0, cfg)[1] == "BAD_BAR"
    df7 = df.drop(index=[110, 111]).reset_index(drop=True)
    tr, _ = B.simulate_trade(B.Series(df7, "4h"), 100, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["gap_cross"] and tr["gap_hours"] == 8.0


# ---------------------------------------------------------------------------- geleceğe bakmama (her varyant)
def _sig(r):
    return (r["variant"], r["side"], r["name"], int(r["t_ms"]), round(float(r["stop"]), 10), r["scopes"])


def _out(r):
    rv = r["r"]
    return _sig(r) + (r["reason"], int(r["hold"]), None if rv != rv else round(float(rv), 10), int(r["exit_bar_ms"]),
                      round(float(r["funding_r"]), 10) if r["funding_r"] == r["funding_r"] else None)


def test_no_lookahead_every_variant_signals_and_trades_are_prefix_invariant(world):
    t_cut = world["t_cut"]
    step = {"4h": H4, "1d": DAY}
    full = [r for r in world["full"] if r["kind"] == "real"]
    trunc = [r for r in world["trunc"] if r["kind"] == "real"]
    seen = set()
    for vid, spec in B.VARIANTS.items():
        f = [r for r in full if r["variant"] == vid and int(r["entry_ms"]) + step[r["tf"]] <= t_cut]
        t = [r for r in trunc if r["variant"] == vid and int(r["entry_ms"]) + step[r["tf"]] <= t_cut]
        assert sorted(map(_sig, f)) == sorted(map(_sig, t)), vid
        done = [r for r in f if int(r["exit_bar_ms"]) + step[r["tf"]] <= t_cut]
        assert set(map(_out, done)) <= set(map(_out, t)), vid
        if done:
            seen.add(vid)
    # geleceği değiştirilmiş tam seride kesimden sonraki işlemler gerçekten farklı (test boş değil)
    assert {_out(r) for r in full} != {_out(r) for r in world["base"] if r["kind"] == "real"}
    must = {"D4_00_BASE", "D4_01_BTC_UP", "D4_03_VOL_OK", "D4_05_STOP3ATR", "D4_06_EXIT_LO20", "D4_12_COST_STOP", "C4_00_BASE",
            "C4_01_REGIME", "C4_02_EXIT_1R", "C4_03_EXIT_3R", "C4_04_WIDE_STOP", "C4_05_COST_STOP", "C4_07_TREND_ONLY",
            "C4_08_REGIME_COST", "FM_00_BASE", "FM_07_EXIT_TREND", "FM_08_EXIT_3R", "FM_CTRL_NO_RSI"}
    assert must <= seen, sorted(must - seen)


def test_no_lookahead_raw_signals_for_every_variant_including_1d_and_rare_ones():
    """Ham sinyaller (süzgeçler dahil) karar penceresinden bağımsız: kesilmiş seri = geleceği değiştirilmiş tam seri."""
    df4 = synth(1300, "4h", ms("2024-01-01"), seed=51, vol=0.012, legs=30, leg_drift=0.008, spike=0.2)
    df1 = synth(900, "1d", ms("2022-01-01"), seed=52, vol=0.03, legs=35, leg_drift=0.012, spike=0.2)
    btc = wave_daily(1400, ms("2021-01-01"), period=50)
    cut4, cut1 = 1100, 800
    for tf, df, cut in (("4h", df4, cut4), ("1d", df1, cut1)):
        t_cut = int(df["timestamp"].iloc[cut])
        fut = df.copy()
        fut.loc[cut:, ["open", "high", "low", "close"]] *= 0.5
        futb = btc.copy()
        futb.loc[futb["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 2.0
        coin_full = df1.copy()
        coin_full.loc[coin_full["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 3.0

        def build(d, b, c1):
            S = B.Series(d, tf)
            cr = B.regime_daily(c1)
            return S, B._masks(S, B.regime_at(S.t, *B.regime_daily(b)), B.regime_at(S.t, *cr))
        Sf, mf = build(fut, futb, coin_full if tf == "4h" else fut)
        St, mt = build(df.iloc[:cut].reset_index(drop=True), tcut(btc, t_cut), tcut(df1, t_cut) if tf == "4h"
                       else df.iloc[:cut].reset_index(drop=True))
        recs_f = B.fm_records(Sf, "X/USDT") if tf == "4h" else []
        recs_t = B.fm_records(St, "X/USDT") if tf == "4h" else []
        fired = []
        for vid, spec in B.VARIANTS.items():
            if spec["tf"] != tf or spec["book"] == "C4":
                continue
            for side in spec["sides"]:
                if spec["book"] == "D4":
                    a, _, _ = B.d4_raw(Sf, spec, mf, side)
                    b, _, _ = B.d4_raw(St, spec, mt, side)
                    a = [int(i) for i in a if i < cut]
                    b = [int(i) for i in b]
                else:
                    a = [(e.i, e.stop, e.trigger) for e in B.fm_raw(Sf, recs_f, spec, mf, side)[0] if e.i < cut]
                    b = [(e.i, e.stop, e.trigger) for e in B.fm_raw(St, recs_t, spec, mt, side)[0]]
                assert a == b, (tf, vid, side)
                if a:
                    fired.append((vid, side))
        if tf == "1d":
            assert {"D4_07_1D", "D4_08_1D_55_20", "D4_09_1D_BTC_UP"} <= {v for v, _ in fired}
            assert ("D4_10_1D_REGIME_BOTH", "LONG") in fired and ("D4_10_1D_REGIME_BOTH", "SHORT") in fired
        else:
            assert {"D4_02_COIN_UP", "D4_04_VOL_CONFIRM", "D4_11_BTC_COIN_UP", "FM_00_BASE", "FM_02_COIN_UP",
                    "FM_03_VOL_OK", "FM_04_VOL_CONFIRM"} <= {v for v, _ in fired}
            assert ("FM_09_REGIME_BOTH", "SHORT") in fired and ("FM_09_REGIME_BOTH", "LONG") in fired
            # Formasyon SHORT (FM_09) = katalogun THREE_BLACK_CROWS SHORT kayıtları + RSI14 < 30 + tetik + ATR + 300 barlık temiz pencere
            spec9 = {**B.VARIANTS["FM_09_REGIME_BOTH"], "filters": []}
            got9 = [e.i for e in B.fm_raw(Sf, recs_f, spec9, mf, B.SHORT)[0]]
            want9 = sorted(e.i for e in recs_f if e.name == "THREE_BLACK_CROWS" and e.side == "SHORT" and e.trigger is not None
                           and Sf.atr_ok[e.i] and Sf.clean(300)[e.i] and Sf.ind["rsi"][e.i] < 30)
            assert got9 == want9 and want9
    # C4: tespit + rejim süzgeci + tekilleştirme (C4_06'nın DSL bağlamı dahil)
    t_cut = int(df4["timestamp"].iloc[cut4])
    fut = df4.copy()
    fut.loc[cut4:, ["open", "high", "low", "close"]] *= 0.5
    Sf, St = B.Series(fut.iloc[:cut4 + 150].reset_index(drop=True), "4h"), B.Series(df4.iloc[:cut4].reset_index(drop=True), "4h")
    df_ = {"f": Sf, "t": St}
    out = {}
    for k, S in df_.items():
        bm = B.regime_at(S.t, *B.regime_daily(btc if k == "t" else btc))
        masks = B._masks(S, bm, None)
        det = B.c4_detect(S, "X/USDT", ["base", "stop1", "volok"])
        for vid, spec in B.C4_VARIANTS.items():
            real = {}
            for cv in spec["cvs"]:
                side = B.c4_variation(cv, None).side
                dm = B.filter_mask(spec["filters"], side, masks, S.n)
                real[cv] = {e.i: e.stop for e in det[spec["detection"]][cv][0] if e.family == "candle_var" and dm[e.i] and e.i < cut4}
            for scope in spec["cells"]:
                w, _ = B.c4_dedup(real, spec["cvs"], scope)
                out.setdefault((vid, scope), {})[k] = sorted((cv, i, real[cv][i]) for cv, i in w)
    for key, v in out.items():
        assert v["f"] == v["t"], key
    assert sum(1 for v in out.values() if v["t"]) >= 20


def test_placebo_is_deterministic_keyed_period_rated_and_q_pool_is_prior_only(mini):
    df4, df1, btc1 = world_frames()
    S = B.Series(df4, "4h")
    masks = B._masks(S, B.regime_at(S.t, *B.regime_daily(btc1)), None)
    spec = B.D4_VARIANTS["D4_00_BASE"]
    raw, elig, _ = B.d4_raw(S, spec, masks, B.LONG)
    bars, info = B._placebo_bars(S, "SYN/USDT", "D4_00_BASE", B.LONG, raw, elig)
    bars2, info2 = B._placebo_bars(S, "SYN/USDT", "D4_00_BASE", B.LONG, raw, elig)
    assert bars == bars2 and info == info2 and bars
    per = np.array([B.period_of(int(t)) or "" for t in S.t])
    for p_ in ("IS", "OOS"):
        m = per == p_
        assert info["raw"][p_] == int(np.isin(np.flatnonzero(m), raw).sum())
        assert info["eligible"][p_] == int((elig & m).sum()) and info["p"][p_] == info["raw"][p_] / info["eligible"][p_]
    for j in bars:                                                                   # anahtar: crc32("<sembol>|<dilim>|<kimlik>|<yön>|<ts>")
        u = zlib.crc32(f"SYN/USDT|4h|D4_00_BASE|LONG|{int(S.ts[j])}".encode()) / 2 ** 32
        assert elig[j] and u < info["p"][per[j]] and per[j] in ("IS", "OOS")
    # Formasyon q havuzu: yalnız j'den ÖNCEKİ ham isabetler; havuz boş → NO_REAL_RISK
    recs = [L.Event("SYN/USDT", "4h", "x", "THREE_WHITE_SOLDIERS", "LONG", i, int(S.ts[i]) + H4, float(S.c[i]) - q * float(S.ind["atr"][i]),
                    trigger=float(S.c[i])) for i, q in ((620, 1.5), (700, 2.5), (800, 3.5))]
    vid = "FM_CTRL_NO_RSI"
    fspec = B.FM_VARIANTS[vid]
    rows, meta = B.fm_series_rows("SYN/USDT", S, B._masks(S, None, None), L.LabConfig(), [vid], recs=recs)
    pl = [r for r in rows if r["kind"] == "placebo"]
    hits, elig_f, _ = B.fm_raw(S, recs, fspec, B._masks(S, None, None), B.LONG)
    pbars, pinf = B._placebo_bars(S, "SYN/USDT", vid, B.LONG, [e.i for e in hits], elig_f)
    assert meta[f"{vid}|LONG"]["placebo"]["skipped"].get("NO_REAL_RISK", 0) == sum(1 for j in pbars if j <= 620) > 0
    for r in pl:
        j = r["i"]
        prior = [q for i, q in ((620, 1.5), (700, 2.5), (800, 3.5)) if i < j]
        q = (float(S.c[j]) - r["stop"]) / float(S.ind["atr"][j])
        k = int(zlib.crc32(f"SYN/USDT|4h|{vid}|q|{int(S.ts[j])}".encode()) / 2 ** 32 * len(prior))
        assert math.isclose(q, prior[k], rel_tol=1e-9)
    assert pl


def test_c4_dedup_order_regime_before_dedup_and_cost_stop_after_without_fallback(mini):
    """§6.2 örnekleri: DOWN rejiminde aynı barda CV001 LONG + CV004 SHORT → C4_01'de işlem CV004; C4_00'da CV001; tek yönlü
    hücreler kendi yönünde; COST_STOP elenen seçilmiş varyasyon alttakine DÜŞMEZ."""
    df4, _, _ = world_frames()
    S = B.Series(df4, "4h")
    i = 700
    a = float(S.ind["atr"][i])
    mk = lambda cv, side, stop, fam="candle_var": L.Event("SYN/USDT", "4h", fam, cv, side, i, int(S.ts[i]) + H4, stop)  # noqa: E731
    c = float(S.c[i])
    evs = {cv: ([], np.full(S.n, a), {}) for cv in B.CV_ORDER}
    evs["CV001_BREAKOUT20_TREND_VOL_L"] = ([mk("CV001_BREAKOUT20_TREND_VOL_L", "LONG", c - 0.5 * a)], np.full(S.n, a), {})
    evs["CV003_PULLBACK_ENGULF_L"] = ([mk("CV003_PULLBACK_ENGULF_L", "LONG", c - 3.0 * a)], np.full(S.n, a), {})
    evs["CV004_PULLBACK_ENGULF_S"] = ([mk("CV004_PULLBACK_ENGULF_S", "SHORT", c + 2.0 * a)], np.full(S.n, a), {})
    det = {"base": evs}
    down = B._masks(S, np.full(S.n, -1, dtype=np.int8), None)
    rows, meta = B.c4_series_rows("SYN/USDT", S, down, L.LabConfig(), ["C4_00_BASE", "C4_01_REGIME", "C4_05_COST_STOP"], det=det)
    pick = {(r["variant"], sc): r["name"] for r in rows if r["kind"] == "real" for sc in r["scopes"].split(",") if sc}
    assert pick[("C4_00_BASE", "BOTH")] == "CV001_BREAKOUT20_TREND_VOL_L"
    assert pick[("C4_00_BASE", "LONG")] == "CV001_BREAKOUT20_TREND_VOL_L"
    assert pick[("C4_00_BASE", "SHORT")] == "CV004_PULLBACK_ENGULF_S"
    assert pick[("C4_01_REGIME", "BOTH")] == "CV004_PULLBACK_ENGULF_S" and ("C4_01_REGIME", "LONG") not in pick
    assert meta["C4_00_BASE"]["suppressed"] == {"BOTH": 2, "LONG": 1, "SHORT": 0}
    # CV001'in stop'u dar (s < %1,6) → C4_05'te elenir ve CV003'e DÜŞÜLMEZ
    s = abs(float(S.o[i + 1]) - (c - 0.5 * a)) / float(S.o[i + 1])
    assert s < B.COST_STOP_MIN
    assert ("C4_05_COST_STOP", "LONG") not in pick and pick[("C4_05_COST_STOP", "SHORT")] == "CV004_PULLBACK_ENGULF_S"
    assert meta["C4_05_COST_STOP"]["cvs"]["CV001_BREAKOUT20_TREND_VOL_L"]["real"]["skipped"] == {"STOP_TOO_TIGHT_FOR_COST": 1}


# ---------------------------------------------------------------------------- fonlama
def test_funding_uses_funding_carry_and_fills_nan_conservatively(mini):
    from tradingbot import futures_lab as FL
    df = synth(N4, "4h", T0, seed=61)
    S = B.Series(df, "4h")
    cfg = L.LabConfig()
    items = []
    for i, side in ((600, "LONG"), (650, "SHORT"), (N4 - 3, "LONG")):
        s = 1 if side == "LONG" else -1
        tr, why = B.simulate_trade(S, i, side, float(S.c[i]) - s * 3 * float(S.ind["atr"][i]),
                                   {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), float(S.ind["atr"][i]), cfg)
        assert why == ""
        items.append(B._row("SYN/USDT", S, "D4", "D4_00_BASE", "real", side, "X", tr, cfg))
    ft = np.arange(MINI["FUNDING_START_MS"], MINI["FUNDING_END_MS"], 8 * HOUR, dtype=np.int64)
    raw = {"f_t": ft, "f_rate": np.full(len(ft), 0.0003)}
    rows = copy.deepcopy(items)
    B.apply_funding(rows, S, raw)
    ref = []
    for r in items:
        ev = L.Event("SYN/USDT", "4h", "book", "T", r["side"], r["i"], r["t_ms"], r["stop"],
                     exit_reason="TIME" if r["reason"] in ("TIME", "DATA_END") else r["reason"], hold=r["hold"])
        ref.append(ev)
    FL.funding_carry(ref, S.arr, S.ts, S.step, raw, names=frozenset({"T"}))
    for r, ev in zip(rows, ref):
        assert r["funding_nan"] is False and r["funding_r"] == ev.funding_r and r["funding_raw"] == ev.funding_r
    assert rows[0]["funding_r"] < 0 < rows[1]["funding_r"]                          # LONG öder, SHORT alır
    assert rows[2]["reason"] == "DATA_END" and rows[2]["funding_nan"] is False      # PRICE_END sonrası uzlaşma var
    rows2 = copy.deepcopy(items)
    B.apply_funding(rows2, S, None)                                                  # fonlama yok → tutucu doldurma
    for r in rows2:
        h = (r["exit_ms"] - r["entry_ms"]) / HOUR
        want = -0.0001 * r["entry"] * math.ceil(h / 8 - 1e-12) / r["risk"]
        assert r["funding_nan"] is True and r["funding_raw"] is None and math.isclose(r["funding_r"], want, rel_tol=1e-12)
        assert r["funding_r"] < 0


# ---------------------------------------------------------------------------- aylık ölçü
def _trades(spec):
    """spec: [(çıkış ayı 'YYYY-MM', R, karar anı ms)] → hücre işlem çerçevesi."""
    rows = []
    for mk, r, t in spec:
        e = B.month_start_ms(mk) + 5 * DAY
        rows.append({"symbol": "X", "t_ms": t, "period": B.period_of(t), "r": r, "rf": r - 0.01, "exit_bar_ms": e, "exit_ms": e + H4,
                     "entry_ms": t, "funding_nan": False, "mtm": {}, "w": 1.0, "reason": "STOP"})
    return pd.DataFrame(rows)


def test_monthly_exit_month_zero_months_and_bootstrap_interval():
    t_is = ms("2024-12-30")
    df = _trades([("2023-03", 2.0, ms("2023-03-01")), ("2025-01", 4.0, t_is), ("2025-01", -1.0, ms("2025-01-02")),
                  ("2026-09", 1.0, ms("2026-09-29"))])
    m = B.monthly(df, "r")
    assert m["IS"]["months"] == 24 and m["OOS"]["months"] == 21
    assert m["IS"]["series"]["2023-03"] == 1.0 and m["IS"]["months_without_trades"] == 23
    # keşifte açılıp doğrulamada kapanan işlem doğrulama ayına yazılır
    assert m["OOS"]["series"]["2025-01"] == 1.5 and m["OOS"]["series"]["2026-09"] == 0.5 and m["OOS"]["months_without_trades"] == 19
    assert math.isclose(m["OOS"]["mean_pct_exact"], 2.0 / 21, rel_tol=1e-12)
    assert m["OOS"]["worst_month"]["pct"] == 0.0 and m["OOS"]["share_ge_target"] == round(1 / 21, 4)
    vals = np.array([m["OOS"]["series"][k] for k in B.OOS_MONTHS])
    idx = np.random.default_rng(20261005).integers(0, 21, size=(10_000, 21))
    means = vals[idx].mean(axis=1)
    assert m["OOS"]["ci95_exact"] == [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]
    w = np.array([1.0, 0.5, 2.0, 1.0])
    mw = B.monthly(df, "r", w)
    assert mw["OOS"]["series"]["2025-01"] == round(0.5 * (0.5 * 4.0 + 2.0 * -1.0), 4)
    assert B.monthly(df, "r", by="t_ms")["IS"]["series"]["2024-12"] == 2.0           # bilgi: karar ayı


# ---------------------------------------------------------------------------- kapasite
def _cands(rows):
    out = []
    for k, (sym, t, exit_t, entry, stop, r) in enumerate(rows):
        out.append({"symbol": sym, "t_ms": t, "exit_ms": exit_t, "exit_bar_ms": exit_t - H4, "entry_ms": t, "entry": entry, "stop": stop,
                    "r": r, "rf": r, "funding_nan": False, "period": B.period_of(t), "reason": "STOP", "mtm": {}})
    return pd.DataFrame(out)


def test_capacity_hand_computed_fit_size_bump_and_no_bump():
    t = ms("2025-03-01")
    table = {"A/USDT": (5.0, 0.001, 0.001), "B/USDT": (100.0, 0.001, 0.001)}
    # s = %2: slot tavanı 0,95 × 200 / 20 × 3 = 28,5 USDT → w = 0,57 (belgedeki örnek)
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 98.0, 1.0)]), "D4", "D4_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 0.57, rel_tol=1e-9)
    # s ≥ %3,51 → tam risk
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 96.0, 1.0)]), "D4", "D4_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 1.0, rel_tol=1e-9)
    # Formasyon (E = 100, K = 30): w = min(1, 19 × s)
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 98.0, 1.0)]), "FM", "FM_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 0.38, rel_tol=1e-9)
    # min-notional 100: BUMP → 100 USDT'ye çıkarılır (risk 2 USDT, w = 2); NO_BUMP → MIN_ORDER_CONFLICT
    c = _cands([("B/USDT", t, t + DAY, 100.0, 98.0, 1.0)])
    rb = B.capacity_run(c, "D4", "D4_00_BASE", table=table, bump=True)
    rn = B.capacity_run(c, "D4", "D4_00_BASE", table=table, bump=False)
    assert math.isclose(rb["accepted"]["w"].iloc[0], 2.0, rel_tol=1e-9) and rb["bumped"] == 1
    assert len(rn["accepted"]) == 0 and rn["rejected"] == {"MIN_ORDER_CONFLICT": 1}
    # PIT: tablo yok → min-notional 0, çıkarma yok
    rp = B.capacity_run(c, "D4", "D4_00_BASE", table=None, bump=False)
    assert math.isclose(rp["accepted"]["w"].iloc[0], 0.57, rel_tol=1e-9)


def test_capacity_slots_same_symbol_exit_frees_slot_and_neutral_order():
    t = ms("2025-03-01")
    table = {f"S{k}/USDT": (5.0, 0.001, 0.001) for k in range(40)}
    rows = [(f"S{k}/USDT", t, t + 10 * H4, 100.0, 96.0, 1.0) for k in range(22)]
    rows += [("S0/USDT", t + H4, t + 5 * H4, 100.0, 96.0, 1.0)]                         # aynı sembol açık
    rows += [("S30/USDT", t + 10 * H4, t + 20 * H4, 100.0, 96.0, 1.0)]                  # çıkışla aynı anda: slot boşalır
    res = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True)
    assert res["rejected"] == {"SLOTS_FULL": 2, "SAME_SYMBOL": 1} and len(res["accepted"]) == 21
    acc = set(res["accepted"]["symbol"])
    first = sorted([f"S{k}/USDT" for k in range(22)], key=lambda s: L._h(s, t, "D4_00_BASE"))
    assert acc == set(first[:20]) | {"S30/USDT"}                                      # aynı t'de _h(sembol, t, kimlik) sırası
    k3 = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True, slots=3)
    assert len(k3["accepted"]) == 4
    cfg_o = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True, order="config",
                           order_list=[f"S{k}/USDT" for k in range(40)])
    assert set(cfg_o["accepted"]["symbol"]) == {f"S{k}/USDT" for k in range(20)} | {"S30/USDT"}
    conc = B.concurrency(res["accepted"])
    assert conc["ALL"]["max"] == 20 and conc["OOS"]["mean"] > 0


def test_capacity_cell_monthly_weights_nan_share_and_filter_wait(tmp_path):
    t = ms("2025-03-01")
    real = _cands([("A/USDT", t, t + DAY, 100.0, 98.0, 2.0), ("B/USDT", t, t + DAY, 100.0, 96.0, -1.0)])
    real.loc[1, "funding_nan"] = True
    real["rf"] = real["r"] - 0.1
    p = tmp_path / "symbol_filters.json"
    p.write_text(json.dumps({"verified_at": "2026-10-05T00:00:00", "futures": {
        "A/USDT": {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"},
        "BUSDT": {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"}}}), encoding="utf-8")
    flt = B.load_filters(p, ["A/USDT", "B/USDT"])
    assert flt["ok"] and flt["verified_at"] and flt["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert not B.load_filters(p, ["A/USDT", "C/USDT"])["ok"] and not B.load_filters(None, ["A/USDT"])["ok"]
    cap = B.capacity_cell(real, "D4", "D4_00_BASE", filters=flt, universe_order=["A/USDT", "B/USDT"])
    for mode in B.CAP_MODES:
        m = cap[mode]
        assert m["accepted"] == 2 and m["nan_funding_share_oos"] == 0.5
        assert math.isclose(m["unfunded"]["OOS"]["series"]["2025-03"], round(0.5 * (0.57 * 2.0 + 1.0 * -1.0), 4), abs_tol=1e-4)
    assert cap["info"]["K3"]["accepted"] == 2


def test_target_status_conditions_and_pooled_direction_rule():
    def cell(scope="LONG", strict=(L.V_STRONG, L.V_STRONG), oos=(1.2, 1.1), lo=0.2, is_=0.3, nan=0.0, flags=(), cap=True):
        st = {f: {"verdict_strict": strict[k], "verdict": strict[k]} for k, f in enumerate(("unfunded", "funded"))}
        m = {"unfunded": {"OOS": {"mean_pct_exact": oos[0]}, "IS": {"mean_pct_exact": 0.5}},
             "funded": {"OOS": {"mean_pct_exact": oos[1], "ci95_exact": [lo, 2.0]}, "IS": {"mean_pct_exact": is_}},
             "nan_funding_share_oos": nan}
        return {"variant": "D4_10_1D_REGIME_BOTH", "scope": scope, "stats": st, "flags": list(flags),
                "capacity": {"BUMP": m, "NO_BUMP": copy.deepcopy(m)} if cap else {"status": B.ST_FILTER_WAIT}}
    ok = cell()
    assert B.target_status(ok, {})["meets"] and B.target_status(ok, {})["status"] == B.ST_MEETS
    assert B.target_status(cell(oos=(1.0, 1.0)), {})["meets"]                         # ≥ +1,0 (eşitlik geçer)
    assert "2:AYLIK_BUMP_funded" in B.target_status(cell(oos=(1.2, 0.9999)), {})["failed"]
    assert "3:ARALIK_BUMP" in B.target_status(cell(lo=0.0), {})["failed"]
    assert "4:KESIF_BUMP" in B.target_status(cell(is_=0.0), {})["failed"]
    assert B.target_status(cell(nan=0.051), {})["status"] == B.ST_FUND_MISSING
    assert B.target_status(cell(strict=(L.V_STRONG, L.V_WEAK)), {})["failed"] == ["1:SIKI_FONLAMALI"]
    assert B.target_status(cell(flags=[B.ST_CENSOR]), {})["failed"] == ["7:" + B.ST_CENSOR]
    assert B.target_status(cell(cap=False), {})["status"] == B.ST_FILTER_WAIT
    assert B.target_status(cell(cap=False, strict=(L.V_WEAK, L.V_WEAK)), {})["status"] == B.ST_NOT_MEETS
    nm = cell()
    nm["capacity"]["NO_BUMP"]["unfunded"]["OOS"]["mean_pct_exact"] = 0.5               # kötü olan biçim belirler
    assert B.target_status(nm, {})["failed"] == ["2:AYLIK_NO_BUMP_unfunded"]
    both = cell(scope="BOTH")
    others = {"D4_10_1D_REGIME_BOTH|LONG": cell(), "D4_10_1D_REGIME_BOTH|SHORT": cell(strict=(L.V_LOSS, L.V_STRONG))}
    assert B.target_status(both, others)["failed"] == ["8:YON_SHORT_unfunded"]


def test_holm_and_multiple_test_pvalue():
    p = {"a": 0.001, "b": 0.012, "c": 0.03, "d": 0.04}
    assert B.holm(p) == {"a": True, "b": True, "c": False, "d": False}               # 0,05/4, 0,05/3 geçer; 0,03 > 0,05/2 → durur
    assert B.holm({"a": 0.02, "b": 0.001}) == {"a": True, "b": True}
    assert B.holm({"a": 0.03, "b": 0.03}) == {"a": False, "b": False}
    t0 = ms("2025-02-01")
    real = pd.DataFrame({"period": "OOS", "r": np.linspace(0.5, 1.5, 40), "t_ms": t0 + np.arange(40) * DAY})
    plac = pd.DataFrame({"period": "OOS", "r": np.linspace(-0.5, 0.5, 40), "t_ms": t0 + np.arange(40) * DAY})
    pv = B.mt_pvalue(real, plac, iters=2000)
    assert pv == 1 / 2001
    assert B.mt_pvalue(real.iloc[:4], plac, iters=200) == 1.0                         # gün < 5 → p = 1
    assert B.mt_pvalue(plac, real, iters=2000) > 0.99


# ---------------------------------------------------------------------------- basamaklar, hücre ölçüleri, fonlama dosyaları
def test_capacity_total_risk_rejection_and_reject_order(monkeypatch):
    """TOTAL_RISK: açık risk + nominal yeni risk (0,005 × E) > E → ret; sıra SAME_SYMBOL, SLOTS_FULL, TOTAL_RISK, sonra fit_size."""
    from types import SimpleNamespace
    from tradingbot import learning_mode as LM
    calls = []

    def fake_fit(**kw):
        calls.append(kw)
        return SimpleNamespace(ok=True, reason=None, risk_usdt=150.0, margin=0.0, size_rule="FIT")
    monkeypatch.setattr(LM, "fit_size", fake_fit)
    t = ms("2025-03-01")
    rows = [("A/USDT", t, t + 10 * H4, 100.0, 96.0, 1.0), ("B/USDT", t + H4, t + 10 * H4, 100.0, 96.0, 1.0),
            ("C/USDT", t + 2 * H4, t + 10 * H4, 100.0, 96.0, 1.0), ("A/USDT", t + 3 * H4, t + 10 * H4, 100.0, 96.0, 1.0),
            ("D/USDT", t + 10 * H4, t + 12 * H4, 100.0, 96.0, 1.0)]
    res = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=None, bump=False)
    # A kabul (0 + 1 ≤ 200), B kabul (150 + 1 ≤ 200), C ret (300 + 1 > 200), A ikinci kez SAME_SYMBOL (TOTAL_RISK'ten önce);
    # D çıkışların kapandığı anda kabul
    assert res["rejected"] == {"TOTAL_RISK": 1, "SAME_SYMBOL": 1} and list(res["accepted"]["symbol"]) == ["A/USDT", "B/USDT", "D/USDT"]
    assert len(calls) == 3                                                           # retlerde fit_size çağrılmaz
    assert B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=None, bump=False, slots=1)["rejected"] == \
        {"SLOTS_FULL": 2, "SAME_SYMBOL": 1}                                           # SLOTS_FULL TOTAL_RISK'ten önce


def _cell_rows(vid, kind, side, period, n, r=0.5, reason="STOP", t0=None, step=DAY, nan_funding=False):
    t0 = (ms("2023-03-01") if period == "IS" else ms("2025-03-01")) if t0 is None else t0
    out = []
    for k in range(n):
        t = t0 + k * step
        out.append({"symbol": "X/USDT", "tf": "4h", "book": B.VARIANTS[vid]["book"], "variant": vid, "kind": kind, "side": side,
                    "name": "N", "i": 1000 + k, "t_ms": t, "period": B.period_of(t), "entry": 100.0, "stop": 98.0, "risk": 2.0,
                    "exit_px": 101.0, "reason": reason, "r": float("nan") if reason == "BAD_BAR" else r + 0.01 * (k % 7),
                    "cost_r": 0.08, "hold": 3, "entry_ms": t, "exit_ms": t + 3 * H4, "exit_bar_ms": t + 2 * H4, "gap_cross": False,
                    "gap_hours": 0.0, "scopes": "", "funding_raw": None if nan_funding else -0.01, "funding_nan": nan_funding,
                    "funding_r": -0.01, "ctx": {"hacim": "normal", "rsi": "50-70", "trend": "yukarı", "volatilite": "normal"},
                    "mtm": {}})
    return out


def test_evaluate_cell_censor_and_bad_bar_shares_and_flags():
    vid = "D4_01_BTC_UP"
    rows = (_cell_rows(vid, "real", "LONG", "IS", 20) + _cell_rows(vid, "real", "LONG", "IS", 1, reason="DATA_END", t0=ms("2024-06-01"))
            + _cell_rows(vid, "real", "LONG", "OOS", 26) + _cell_rows(vid, "real", "LONG", "OOS", 4, reason="DATA_END", t0=ms("2026-09-01"))
            + _cell_rows(vid, "real", "LONG", "OOS", 1, reason="DELISTED", t0=ms("2026-08-01"))
            + _cell_rows(vid, "real", "LONG", "OOS", 1, reason="BAD_BAR", t0=ms("2026-07-01"))
            + _cell_rows(vid, "placebo", "LONG", "IS", 25, r=0.0) + _cell_rows(vid, "placebo", "LONG", "OOS", 25, r=0.0))
    T = B.trades_frame(rows)
    T["rf"] = T["r"] + T["funding_r"]
    c = B.evaluate_cell(T, f"{vid}|LONG", L.LabConfig())
    # sansür payı = DATA_END / BAD_BAR olmayan gerçek işlemler (dönem karar anına göre); DELISTED sansüre girmez, ayrıca sayılır
    assert c["censor_share"] == {"IS": round(1 / 21, 6), "OOS": round(4 / 31, 6)} and c["counts"]["delisted"] == 1
    assert c["bad_share"] == round(1 / 53, 6) and c["counts"]["bad_bar"] == 1                 # BAD_BAR payı bütün gerçek işlemlerde
    assert c["flags"] == [B.ST_CENSOR, B.ST_BAD]
    assert c["counts"]["real"] == {"IS": 21, "OOS": 31} and c["counts"]["placebo"] == {"IS": 25, "OOS": 25}
    # eşiğin altı: sansür %10 ve BAD_BAR %1 tam sınırda geçer (> ile işaretlenir)
    rows2 = (_cell_rows(vid, "real", "LONG", "OOS", 99) + _cell_rows(vid, "real", "LONG", "OOS", 11, reason="DATA_END", t0=ms("2026-09-01"))
             + _cell_rows(vid, "real", "LONG", "OOS", 1, reason="BAD_BAR", t0=ms("2026-07-01"))
             + _cell_rows(vid, "placebo", "LONG", "OOS", 25, r=0.0))
    T2 = B.trades_frame(rows2)
    T2["rf"] = T2["r"] + T2["funding_r"]
    c2 = B.evaluate_cell(T2, f"{vid}|LONG", L.LabConfig())
    assert c2["censor_share"]["OOS"] == 0.1 and c2["bad_share"] == round(1 / 111, 6) and c2["flags"] == []


def test_side_stats_vs_placebo_needs_placebo_n_20_in_both_periods():
    cfg = L.LabConfig()
    vid = "D4_01_BTC_UP"

    def frame(rows):
        T = B.trades_frame(rows)
        T["rf"] = T["r"] + T["funding_r"]
        return T
    real = frame(_cell_rows(vid, "real", "LONG", "IS", 25, r=0.4) + _cell_rows(vid, "real", "LONG", "OOS", 25, r=0.6))
    for n_is, n_oos, ok in ((20, 19, False), (19, 20, False), (20, 20, True)):
        plac = frame(_cell_rows(vid, "placebo", "LONG", "IS", n_is, r=0.1) + _cell_rows(vid, "placebo", "LONG", "OOS", n_oos, r=0.0))
        st = B.side_stats(real, plac, "r", cfg)
        assert (st["vs_placebo"] is not None) is ok, (n_is, n_oos)
        if ok:
            assert st["vs_placebo"]["OOS"] == round(st["OOS"]["mean_r"] - st["placebo"]["OOS"]["mean_r"], 4)
            assert st["vs_placebo"]["IS"] == round(st["IS"]["mean_r"] - st["placebo"]["IS"]["mean_r"], 4)
            assert st["vs_placebo"]["placebo_n"] == [20, 20] and st["vs_placebo"]["ci95"]["OOS"] is not None
    # gerçek tarafta bir dönem boşsa karşılaştırma yok
    only_oos = frame(_cell_rows(vid, "real", "LONG", "OOS", 25, r=0.6))
    plac = frame(_cell_rows(vid, "placebo", "LONG", "IS", 30, r=0.1) + _cell_rows(vid, "placebo", "LONG", "OOS", 30, r=0.0))
    assert B.side_stats(only_oos, plac, "r", cfg)["vs_placebo"] is None


def _fake_cell(cid, *, real_cand=False, plac_cand=False, thin=False):
    vid, scope = cid.split("|")
    s = {"verdict": L.V_THIN if thin else L.V_WEAK, "verdict_strict": L.V_WEAK, "replicated": real_cand, "IS": {"mean_r": 0.1},
         "OOS": {"mean_r": 0.1}, "placebo_verdict": L.V_WEAK, "placebo_replicated": plac_cand, "placebo": {"IS": {"mean_r": 0.1}}}
    return {"cell": cid, "variant": vid, "scope": scope, "book": B.VARIANTS[vid]["book"], "flags": [],
            "counts": {"real": {"IS": 0, "OOS": 0}, "placebo": {"IS": 0, "OOS": 0}},
            "stats": {"unfunded": s, "funded": copy.deepcopy(s)}}


def _report_with(monkeypatch, *, meets, pvals, real_cand=(), plac_cand=(), thin=(), books=B.BOOKS):
    monkeypatch.setattr(B, "evaluate_cell", lambda T, cid, cfg: _fake_cell(cid, real_cand=cid in real_cand, plac_cand=cid in plac_cand,
                                                                           thin=cid in thin))
    monkeypatch.setattr(B, "target_status", lambda c, cells: {"meets": c["cell"] in meets, "failed": [] if c["cell"] in meets else ["2:X"],
                                                              "status": B.ST_MEETS if c["cell"] in meets else B.ST_NOT_MEETS})
    tag = lambda cid: pd.DataFrame({"reason": ["STOP"], "cid": [cid]})  # noqa: E731
    monkeypatch.setattr(B, "cell_frames", lambda T, cid: (tag(cid), tag(cid)))
    monkeypatch.setattr(B, "mt_pvalue", lambda real, plac: pvals.get(real["cid"].iloc[0], 0.9))
    return B.build_report(B.trades_frame([]), L.LabConfig(), universe="primary", filters=None, books=books)


def test_build_report_steps_holm_candidate_rate_and_book_notes(monkeypatch):
    """§8.5 basamakları build_report içinde: Holm, aday oranı (gerçek ≤ plasebo → TESADÜFLE AÇIKLANABİLİR), taban / veri
    gözetlemeli dışlama, PIT TEYİDİ BEKLİYOR; eksik hücre kümesinde çoklu test yapılmaz; §8.4 defter notu."""
    meets = {"D4_01_BTC_UP|LONG", "D4_00_BASE|LONG", "C4_07_TREND_ONLY|BOTH", "D4_02_COIN_UP|LONG", "FM_01_BTC_UP|LONG"}
    pvals = {"D4_01_BTC_UP|LONG": 1e-5, "D4_00_BASE|LONG": 1e-5, "C4_07_TREND_ONLY|BOTH": 1e-5, "D4_02_COIN_UP|LONG": 0.04}
    real_cand = set(B.PRIMARY_CELLS[:10])
    rep = _report_with(monkeypatch, meets=meets, pvals=pvals, real_cand=real_cand)
    f = {c: rep["cells"][c]["final"] for c in meets}
    assert f == {"D4_01_BTC_UP|LONG": B.ST_PIT_WAIT, "D4_00_BASE|LONG": B.ST_BASE, "C4_07_TREND_ONLY|BOTH": B.ST_BASE,
                 "D4_02_COIN_UP|LONG": B.ST_MT_FAIL, "FM_01_BTC_UP|LONG": B.ST_MT_FAIL}
    assert rep["holm"]["rejected"] == sorted(["D4_01_BTC_UP|LONG", "D4_00_BASE|LONG", "C4_07_TREND_ONLY|BOTH"])  # 0,04 > 0,05/51
    assert rep["candidate_rate"] == {"real": round(10 / 54, 4), "placebo": 0.0} and rep["chance_explains"] is False
    assert rep["cells"]["D4_03_VOL_OK|LONG"]["final"] == B.ST_NOT_MEETS and rep["cells"]["FM_CTRL_NO_RSI|LONG"]["final"].startswith("KONTROL")
    assert rep["conclusion_tr"].startswith("HEDEFİ KARŞILAYIP çoklu testi geçen ve PIT teyidi bekleyen hücre: D4_01_BTC_UP|LONG "
                                           "(D4 defterinin 13 varyantından biri; defterin 15 birincil hücresi var)")
    assert rep["meets_target_detail"]["C4_07_TREND_ONLY|BOTH"] == "C4 defterinin 9 varyantından biri; defterin 27 birincil hücresi var"
    assert rep["meets_target_detail"]["FM_01_BTC_UP|LONG"] == "Formasyon defterinin 10 varyantından biri; defterin 12 birincil hücresi var"
    assert "Hedefi karşılayan hücreler (§8.4)" in B.render_md({**rep, "version": "book_v1"})
    # gerçek aday oranı ≤ plasebo oranı (eşitlik dahil) → geçen her hücre TESADÜFLE AÇIKLANABİLİR; VERİ AZ paydadan çıkar
    plac_cand = set(B.PRIMARY_CELLS[20:30])
    rep = _report_with(monkeypatch, meets=meets, pvals=pvals, real_cand=real_cand, plac_cand=plac_cand)
    assert rep["chance_explains"] is True and rep["candidate_rate"]["real"] == rep["candidate_rate"]["placebo"]
    assert {c: rep["cells"][c]["final"] for c in meets if pvals.get(c, 1) < 1e-3} == {
        "D4_01_BTC_UP|LONG": B.ST_CHANCE, "D4_00_BASE|LONG": B.ST_CHANCE, "C4_07_TREND_ONLY|BOTH": B.ST_CHANCE}
    assert rep["conclusion_tr"].startswith("Hiçbir hücre ÖNERİ ADAYI değil. HEDEFİ KARŞILAYIP sonraki basamakta elenen: ")
    assert "D4_01_BTC_UP|LONG (D4 defterinin 13 varyantından biri" in rep["conclusion_tr"] and B.NO_TARGET_TR not in rep["conclusion_tr"]
    thin = set(B.PRIMARY_CELLS[40:])
    rep = _report_with(monkeypatch, meets=meets, pvals=pvals, real_cand=real_cand, plac_cand=plac_cand, thin=thin)
    assert rep["candidate_rate"]["real"] == round(10 / 40, 4)
    # yalnız bir defter: 54 hücrenin hepsi yok → çoklu test yapılmaz, hedefi karşılayan hücre öneriye ilerleyemez
    rep = _report_with(monkeypatch, meets=meets, pvals=pvals, real_cand=real_cand, books=["D4"])
    assert rep["complete"] is False and rep["cells"]["D4_01_BTC_UP|LONG"]["final"] == B.ST_MT_INCOMPLETE
    assert rep["cells"]["D4_01_BTC_UP|LONG"]["multiple"]["holm_pass"] is None
    # hiçbir hücre hedefi karşılamıyorsa ön kayıtlı metin
    rep = _report_with(monkeypatch, meets=set(), pvals=pvals)
    assert rep["conclusion_tr"].startswith(f"Hiçbir hücre ÖNERİ ADAYI değil: {B.NO_TARGET_TR}")


def test_final_status_order():
    meets = {"meets": True, "status": B.ST_MEETS}
    fs = B.final_status
    assert fs({"meets": False, "status": B.ST_FUND_MISSING}, "D4_01_BTC_UP|LONG", complete=True, holm_pass=True, chance=False) == \
        (B.ST_FUND_MISSING, [])
    assert fs(meets, "D4_01_BTC_UP|LONG", complete=False, holm_pass=True, chance=False) == (B.ST_MT_INCOMPLETE, ["2"])
    assert fs(meets, "D4_01_BTC_UP|LONG", complete=True, holm_pass=False, chance=False) == (B.ST_MT_FAIL, ["2"])
    assert fs(meets, "D4_01_BTC_UP|LONG", complete=True, holm_pass=True, chance=True) == (B.ST_CHANCE, ["2"])
    for cid in ("D4_00_BASE|LONG", "C4_00_BASE|SHORT", "FM_00_BASE|LONG", "C4_07_TREND_ONLY|LONG"):
        assert fs(meets, cid, complete=True, holm_pass=True, chance=False) == (B.ST_BASE, ["3"])
    assert fs(meets, "FM_09_REGIME_BOTH|BOTH", complete=True, holm_pass=True, chance=False) == (B.ST_PIT_WAIT, ["4"])


def test_cell_counts_no_real_risk_share_denominators():
    cv_l = "CV001_BREAKOUT20_TREND_VOL_L"
    meta = {"X/USDT": {
        "C4": {"counts": {"C4_00_BASE": {"cvs": {cv_l: {"detected": 3, "real": {"skipped": {"CHASE": 1}}, "placebo": {
            "drawn": 5, "produced_all": 8, "skipped": {"BAD_BAR": 1},
            "variation_events_skipped": {f"PLACEBO_{cv_l}:NO_REAL_RISK": 2, f"PLACEBO_{cv_l}:BAD_STOP": 1}}}}}}},
        "D4": {"counts": {"D4_00_BASE|LONG": {"real": {"raw_all": 7, "unclean_window": 1}, "placebo": {
            "drawn": 10, "skipped": {"NO_REAL_RISK": 0}}}}},
        "FM": {"counts": {"FM_00_BASE|LONG": {"real": {"raw_all": 4}, "placebo": {"drawn": 10, "skipped": {"NO_REAL_RISK": 4}}}}}}}
    c = B.cell_counts(meta, "C4_00_BASE|LONG")
    # C4: payda = üretilen (süzgeçten önce) + NO_REAL_RISK + BAD_STOP = 11 (D4/Formasyon'daki çekilen barla aynı anlam)
    assert c["placebo_selected"] == 11 and c["no_real_risk_share"] == round(2 / 11, 6) and c["placebo_drawn"] == 5
    assert c["placebo_skipped"] == {"BAD_BAR": 1, "NO_REAL_RISK": 2, "BAD_STOP": 1} and c["real_skipped"] == {"CHASE": 1}
    assert B.cell_counts(meta, "C4_00_BASE|SHORT")["placebo_selected"] == 0               # SHORT CV yok
    f = B.cell_counts(meta, "FM_00_BASE|LONG")
    assert f["placebo_selected"] == f["placebo_drawn"] == 10 and f["no_real_risk_share"] == 0.4
    assert B.cell_counts(meta, "D4_00_BASE|LONG")["no_real_risk_share"] == 0.0


def test_load_funding_monthly_only_post_end_and_window_clip(mini):
    """Yalnız aylık dosyalar (arşivde günlük fundingRate yok); PRICE_END'in ayı da istenir (PRICE_END sonrası uzlaşma);
    pencere [FUNDING_START, FUNDING_END) kırpılır; eksik aylar yazılır."""
    a = "SYNUSDT"
    url = lambda mk: f"{L.ARCHIVE_BASE}/monthly/fundingRate/{a}/{a}-fundingRate-{mk}.zip"  # noqa: E731
    files = {url("2022-03"): funding_zip(B.month_start_ms("2022-03"), extra=[(MINI["FUNDING_START_MS"] - 8 * HOUR, 0.5)]),
             url("2022-05"): funding_zip(B.month_start_ms("2022-05")), url("2022-06"): funding_zip(B.month_start_ms("2022-06")),
             url("2022-07"): funding_zip(B.month_start_ms("2022-07"))}
    asked = []
    raw, info = B.load_funding("SYN/USDT", lambda u: (asked.append(u), files.get(u))[1])
    assert asked == [url(mk) for mk in ("2022-03", "2022-04", "2022-05", "2022-06", "2022-07")]
    assert info["missing"] == ["2022-04"] and set(info["files_sha256"]) == {url(mk) for mk in ("2022-03", "2022-05", "2022-06", "2022-07")}
    assert raw["f_t"].min() == MINI["FUNDING_START_MS"] and raw["f_t"].max() < MINI["FUNDING_END_MS"] and 0.5 not in raw["f_rate"]
    assert info["post_end"] == 3 and info["rows"] == 31 * 3 + 31 * 3 + 30 * 3 + 3
    assert np.all(np.diff(raw["f_t"]) > 0)
    # PRICE_END ayının dosyası yoksa (henüz yayımlanmadı) PRICE_END sonrası uzlaşma yok → koşu veri hatasıyla durur
    del files[url("2022-07")]
    _, info2 = B.load_funding("SYN/USDT", files.get)
    assert info2["post_end"] == 0 and info2["missing"] == ["2022-04", "2022-07"]
    df = flat_series(N4, t0=T0)
    assert "2022-07" in B.data_problem("SYN/USDT", df, None, info2, strict=True)
    assert B.data_problem("SYN/USDT", df, None, info, strict=True) is None
    early = df.iloc[:-1]
    assert "PRICE_END'den önce" in B.data_problem("SYN/USDT", early, None, info, strict=True)
    assert B.data_problem("SYN/USDT", early, None, info2, strict=False) is None          # PIT: DELISTED, fonlama sonu gerekmez
    assert "1d" in B.data_problem("SYN/USDT", df, df.iloc[:0], info, strict=True)


def test_funding_after_price_end_comes_from_the_month_file_and_makes_data_end_funded(mini):
    """PRICE_END'de kapanan (DATA_END) işlemin fonlaması: PRICE_END ayının dosyasındaki uzlaşmalarla NaN değil; o dosya
    olmadan NaN (koşu bu durumda zaten başlamaz)."""
    df = synth(N4, "4h", T0, seed=61)
    S = B.Series(df, "4h")
    cfg = L.LabConfig()
    i = N4 - 3
    tr, why = B.simulate_trade(S, i, "LONG", float(S.c[i]) - 3 * float(S.ind["atr"][i]), {"kind": "target", "rr": 9.0, "max_bars": 24},
                               (0.1, 9.0), float(S.ind["atr"][i]), cfg)
    assert why == "" and tr["reason"] == "DATA_END"
    a = "SYNUSDT"
    files = {f"{L.ARCHIVE_BASE}/monthly/fundingRate/{a}/{a}-fundingRate-{mk}.zip": funding_zip(B.month_start_ms(mk))
             for mk in ("2022-03", "2022-04", "2022-05", "2022-06", "2022-07")}
    for keep_july, nan in ((True, False), (False, True)):
        fs = dict(files) if keep_july else {k: v for k, v in files.items() if not k.endswith("2022-07.zip")}
        raw, _ = B.load_funding("SYN/USDT", fs.get)
        rows = [B._row("SYN/USDT", S, "D4", "D4_00_BASE", "real", "LONG", "X", tr, cfg)]
        B.apply_funding(rows, S, raw)
        assert rows[0]["funding_nan"] is nan


def test_write_trades_is_byte_deterministic_and_hashes_the_csv_text(tmp_path, world):
    import gzip
    T = B.trades_frame(world["base"][:300])
    h1 = B.write_trades(tmp_path / "a.csv.gz", T)
    h2 = B.write_trades(tmp_path / "b.csv.gz", T)
    a, b = (tmp_path / "a.csv.gz").read_bytes(), (tmp_path / "b.csv.gz").read_bytes()
    assert a == b and h1 == h2 == hashlib.sha256(gzip.decompress(a)).hexdigest()
    back = B.read_trades(tmp_path / "a.csv.gz")
    assert len(back) == len(T) == 300 and np.allclose(back["r"], T["r"].astype(float), equal_nan=True)


def test_pit_missing_pairs_counts_a_not_loaded_symbol_as_missing():
    m = B.month_start_ms("2023-03")
    d1 = pd.DataFrame({"timestamp": m + DAY * np.arange(31)})
    d4 = pd.DataFrame({"timestamp": m + H4 * np.arange(31 * 6)})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(B, "PIT_MONTHS", ["2023-03"])
        r = B.pit_missing_pairs({"2023-03": ["A/USDT", "C/USDT"]}, {"A/USDT": d4}, {"A/USDT": d1})
    assert r["pairs"] == 2 and r["missing"] == 1 and r["ok"] is False and r["rows"][0]["symbol"] == "C/USDT"


def test_formasyon_short_side_is_three_black_crows_with_strict_rsi_below_30():
    """FM_09 SHORT: katalogun THREE_BLACK_CROWS SHORT kaydı + RSI14 < 30 kesin (30 geçmez, NaN geçmez), tetik şart, ATR ve temiz
    pencere; LONG adı/yönü SHORT'a karışmaz."""
    df = synth(700, "4h", ms("2024-01-01"), seed=33, vol=0.012)
    S = B.Series(df, "4h")
    spec = {**B.FM_VARIANTS["FM_09_REGIME_BOTH"], "filters": []}
    S.ind["rsi"] = rsi = np.array(S.ind["rsi"], dtype=float, copy=True)          # yazılabilir kopya (RSI değerleri elle)
    vals = {400: 29.0, 410: 30.0, 420: float("nan"), 430: 29.99, 440: 10.0, 450: 20.0, 460: 75.0}
    for i, v in vals.items():
        rsi[i] = v

    def ev(i, name, side, trig=True):
        a = float(S.ind["atr"][i])
        return L.Event("X/USDT", "4h", "catalog", name, side, i, int(S.ts[i]) + H4, float(S.c[i]) + (a if side == "SHORT" else -a),
                       trigger=float(S.c[i]) if trig else None)
    recs = [ev(400, "THREE_BLACK_CROWS", "SHORT"), ev(410, "THREE_BLACK_CROWS", "SHORT"), ev(420, "THREE_BLACK_CROWS", "SHORT"),
            ev(430, "THREE_BLACK_CROWS", "SHORT"), ev(440, "THREE_BLACK_CROWS", "SHORT", trig=False),
            ev(450, "THREE_WHITE_SOLDIERS", "SHORT"), ev(460, "THREE_WHITE_SOLDIERS", "LONG")]
    masks = B._masks(S, None, None)
    hits, _, _ = B.fm_raw(S, recs, spec, masks, B.SHORT)
    assert [e.i for e in hits] == [400, 430]
    assert [e.i for e in B.fm_raw(S, recs, spec, masks, B.LONG)[0]] == [460]
    assert spec["signals"]["SHORT"] == {"name": "THREE_BLACK_CROWS", "rsi_op": "<", "rsi": 30.0}
    # SHORT plasebo stop'u koruyucu tarafta (yukarıda): kayıt stop'u c[i] + 1 ATR → q = s × (c[i] − stop) / ATR = 1 → c[j] + ATR[j]
    rsi[300:] = 20.0
    many = [ev(i, "THREE_BLACK_CROWS", "SHORT") for i in range(300, 690, 5)]
    down = B._masks(S, np.full(S.n, -1, dtype=np.int8), None)
    rows, meta = B.fm_series_rows("X/USDT", S, down, L.LabConfig(), ["FM_09_REGIME_BOTH"], recs=many)
    pl = [r for r in rows if r["side"] == "SHORT" and r["kind"] == "placebo"]
    assert len(pl) >= 5 and len([r for r in rows if r["side"] == "SHORT" and r["kind"] == "real"]) >= 20
    for r in pl:
        j = r["i"]
        assert math.isclose(r["stop"], float(S.c[j]) + float(S.ind["atr"][j]), rel_tol=1e-12) and r["name"] == "PLACEBO_THREE_BLACK_CROWS"
    assert meta["FM_09_REGIME_BOTH|LONG"]["real"]["raw_all"] == 0                       # BTC_DOWN'da LONG yok (REGIME_FOLLOW)


def test_d4_08_entry_is_the_55_bar_breakout_and_exit_is_the_20_bar_low():
    df = synth(900, "1d", ms("2022-01-01"), seed=52, vol=0.03, legs=35, leg_drift=0.012, spike=0.2)
    S = B.Series(df, "1d")
    cfg = L.LabConfig()
    spec = B.D4_VARIANTS["D4_08_1D_55_20"]
    raw, _, _ = B.d4_raw(S, spec, B._masks(S, None, None), B.LONG)
    h, lo, c, a = S.h, S.l, S.c, S.ind["atr"]
    n = S.n
    hi55 = np.array([h[i - 55:i].max() if i >= 55 else np.nan for i in range(n)])
    lo20 = np.array([lo[i - 20:i].min() if i >= 20 else np.nan for i in range(n)])
    clean = S.clean(210)
    want = [i for i in range(210, n) if c[i] > hi55[i] and c[i - 1] <= hi55[i - 1] and S.atr_ok[i] and clean[i]]
    assert [int(i) for i in raw] == want and len(want) >= 3
    checked = 0
    for i in raw:
        j, stop = i + 1, float(c[i] - 2.0 * a[i])
        tr, why = B.simulate_trade(S, i, B.LONG, stop, spec["exit"], tuple(spec["risk_atr"]), float(a[i]), cfg)
        if why:
            continue
        exp = None
        for k in range(j, min(n, j + 300)):
            if lo[k] <= stop:
                exp = ("STOP", k)
                break
            if c[k] < lo20[k]:
                exp = ("RULE", k + 1) if k + 1 < n else ("DELISTED", k)
                break
        if exp is None:
            exp = ("TIME", j + 299) if j + 300 <= n else ("DELISTED", n - 1)
        assert (tr["reason"], tr["xb"]) == exp, i
        checked += 1
    assert checked >= 3 and spec["exit"] == {"kind": "channel", "ref": {"LONG": "lo20"}, "max_bars": 300}


def test_book_note_counts_hypothesis_variants_and_primary_cells():
    assert B.book_note("D4_10_1D_REGIME_BOTH|SHORT") == "D4 defterinin 13 varyantından biri; defterin 15 birincil hücresi var"
    assert B.book_note("C4_01_REGIME|BOTH") == "C4 defterinin 9 varyantından biri; defterin 27 birincil hücresi var"
    assert B.book_note("FM_00_BASE|LONG") == "Formasyon defterinin 10 varyantından biri; defterin 12 birincil hücresi var"


# ---------------------------------------------------------------------------- PIT
def test_pit_pool_ranking_listing_and_missing_pairs():
    names = ["BTCUSDT", "BTCUSDT_210326", "ETHBUSD", "AERGOUSDTSETTLED", "TSLAUSDT", "BTCDOMUSDT", "USDCUSDT", "XAUUSDT",
             "1000PEPEUSDT", "PAXGUSDT", "SOLUSDT", "龙虾USDT"]
    pool, excl = B.pit_pool(names)
    assert pool == sorted(["BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "龙虾USDT"])
    assert excl["TSLAUSDT"] == "hisse_etf_halka_arz_oncesi" and excl["XAUUSDT"] == "emtia_doviz" and excl["USDCUSDT"] == "stabil"
    assert excl["BTCDOMUSDT"] == "endeks" and "alt çizgi" in excl["BTCUSDT_210326"] and "USDT" in excl["ETHBUSD"]
    m = B.month_start_ms("2023-03")
    days = lambda n, qv, end=m: pd.DataFrame({"timestamp": end - DAY * np.arange(n, 0, -1), "quote_volume": qv})  # noqa: E731
    frames = {"AUSDT": days(30, 10.0), "BUSDT": days(30, 20.0), "CUSDT": days(29, 1e9), "DUSDT": days(40, 20.0),
              **{f"X{k}USDT": days(30, 1.0) for k in range(45)}}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(B, "PIT_MONTHS", ["2023-03"])
        uni = B.pit_universe(sorted(frames), frames.get)
    top = uni["2023-03"]
    assert len(top) == 40 and top[:3] == ["B/USDT", "D/USDT", "A/USDT"] and "C/USDT" not in top   # en az 30 bar; eşitlikte ad
    assert top[3:] == [f"X{k}/USDT" for k in sorted(range(45), key=lambda k: f"X{k}USDT")][:37]
    pages = ['<ListBucketResult><IsTruncated>true</IsTruncated><NextMarker>data/futures/um/monthly/klines/B/</NextMarker>'
             '<CommonPrefixes><Prefix>data/futures/um/monthly/klines/AUSDT/</Prefix></CommonPrefixes></ListBucketResult>',
             '<ListBucketResult><IsTruncated>false</IsTruncated><CommonPrefixes><Prefix>data/futures/um/monthly/klines/BUSDT/'
             '</Prefix></CommonPrefixes></ListBucketResult>']
    asked = []
    assert B.list_um_symbols(lambda u: (asked.append(u), pages[len(asked) - 1])[1]) == ["AUSDT", "BUSDT"]
    assert "marker=" in asked[1]
    d1 = pd.DataFrame({"timestamp": m + DAY * np.arange(31)})
    d4 = pd.DataFrame({"timestamp": m + H4 * np.arange(31 * 6)})
    mp_ = B.pit_missing_pairs({"2023-03": ["A/USDT", "B/USDT"]}, {"A/USDT": d4, "B/USDT": d4.iloc[:100]}, {"A/USDT": d1, "B/USDT": d1})
    assert mp_["pairs"] == 2 and mp_["missing"] == 1 and mp_["ok"] is False


def test_parse_daily_quote_volume_with_and_without_header_and_microseconds():
    df = pd.DataFrame({"timestamp": [ms("2025-02-01"), ms("2025-02-02")], "open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
                       "close": [1.5, 2.5], "volume": [10.0, 20.0]})
    for header, micro in ((True, False), (False, False), (True, True)):
        q = B.parse_daily_qv(kline_zip(df, header=header, micro=micro))
        assert q["timestamp"].tolist() == df["timestamp"].tolist() and q["quote_volume"].tolist() == [15.0, 50.0]


# ---------------------------------------------------------------------------- uçtan uca (sahte arşiv, ağ YOK)
def kline_zip(df, header=True, micro=False):
    buf = io.BytesIO()
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"] \
        if header else []
    for t, o, h, lo, c, v in df[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False):
        tt = int(t) * (1000 if micro else 1)
        lines.append(f"{tt},{o!r},{h!r},{lo!r},{c!r},{v!r},{tt + 1},{v * c!r},1,0,0,0")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("k.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def funding_zip(m0, rate=0.0001, extra=()):
    """Bir ayın 8 saatlik uzlaşmaları (aylık dosya biçimi); `extra`: eklenecek (zaman, oran) satırları."""
    buf = io.BytesIO()
    rows = [(m0 + k * 8 * HOUR, rate) for k in range(31 * 3) if m0 + k * 8 * HOUR < L._next_month(m0)] + list(extra)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(f"{t},8,{r}" for t, r in rows))
    return buf.getvalue()


def fake_archive(frames, funding_syms, rate=0.0001, last_funding_month="2022-07"):
    """{(sembol, dilim): df} → URL → zip (aylık); fonlama YALNIZ aylık dosyalar (gerçek arşiv gibi: günlük fundingRate yok),
    PRICE_END'in ayı dahil; bilinmeyen dosya None (404)."""
    files = {}
    for (sym, tf), df in frames.items():
        a = B.archive_symbol(sym)
        for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
            files[f"{L.ARCHIVE_BASE}/monthly/klines/{a}/{tf}/{a}-{tf}-{mon}.zip"] = kline_zip(g, header=(mon > "2022-03"))
    for sym in funding_syms:
        a = B.archive_symbol(sym)
        for mon in B._month_range("2022-01", last_funding_month):
            files[f"{L.ARCHIVE_BASE}/monthly/fundingRate/{a}/{a}-fundingRate-{mon}.zip"] = funding_zip(B.month_start_ms(mon), rate)
    return files


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ağ kullanılmamalı")
    monkeypatch.setattr(L, "_http_get", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def _mini_frames():
    df4, df1, btc1 = world_frames()
    btc4 = synth(N4, "4h", T0, seed=71, vol=0.01, legs=50, leg_drift=0.003)
    return {("BTC/USDT", "4h"): btc4, ("BTC/USDT", "1d"): btc1, ("SYN/USDT", "4h"): df4, ("SYN/USDT", "1d"): df1}


def test_end_to_end_dry_run_offline_report_only_no_network_and_cli(tmp_path, monkeypatch, mini):
    _no_network(monkeypatch)
    frames = _mini_frames()
    df4 = frames[("SYN/USDT", "4h")]
    files = fake_archive(frames, ["BTC/USDT", "SYN/USDT"])
    asked = []
    fetch = lambda url: (asked.append(url), files.get(url))[1]  # noqa: E731
    flt = tmp_path / "symbol_filters.json"
    flt.write_text(json.dumps({"verified_at": "2026-10-05T00:00:00", "futures": {
        s: {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"} for s in ("BTC/USDT", "SYN/USDT")}}), encoding="utf-8")
    out, cache = tmp_path / "out", tmp_path / "cache"
    now = ms("2022-08-01")
    rep = B.run(cache_dir=cache, out_dir=out, books=B.BOOKS, filters_path=flt, fetch=fetch, now_ms=now, log=lambda s: None,
                symbols_override=["BTC/USDT", "SYN/USDT", "NODATA/USDT"], code_state=CODE)
    assert rep["registry_sha"] == PINNED_SHA and rep["symbols_overridden"] is True and rep["complete"] is True
    assert set(rep["cells"]) == set(B.ALL_CELLS) and len(rep["cells"]) == 55
    # arşivde hiç verisi olmayan coin dışarıda kalır ve sonuçta yazılır (§11.6); kod durumu rapora yazılır
    assert rep["empty_symbols"] == ["NODATA/USDT"] and "NODATA/USDT" in rep["conclusion_tr"] and rep["code"] == CODE
    c = rep["cells"]["D4_00_BASE|LONG"]
    assert c["counts"]["real"]["IS"] + c["counts"]["real"]["OOS"] > 0 and "BUMP" in c["capacity"]
    assert all(cc["final"] for cc in rep["cells"].values()) and rep["conclusion_tr"]
    assert all(0 <= cc["multiple"]["p"] <= 1 for k, cc in rep["cells"].items() if k in B.PRIMARY_CELLS)
    q = rep["data_quality"]
    assert q["SYN/USDT 4h"]["bars"] == N4 and q["SYN/USDT 4h"]["missing_ratio"] == 0.0 and q["SYN/USDT 4h"]["bad_bars"] == 0
    assert q["SYN/USDT 4h"]["sha256"] == B.series_digest(df4) and q["BTC/USDT 1d"]["first"] == "2020-11-27T00:00:00Z"
    assert rep["funding"]["SYN/USDT"]["rows"] > 0 and rep["funding_files_sha256"]["SYN/USDT"]
    assert rep["funding"]["SYN/USDT"]["post_end"] == 3 and "2022-07" not in rep["funding"]["SYN/USDT"]["missing"]
    assert rep["coverage"]["OOS"]["coins_full"] == 2
    js = json.loads((out / "book_lab_report.json").read_text(encoding="utf-8"))
    md = (out / "book_lab_report.md").read_text(encoding="utf-8")
    assert js["registry_sha"] == PINNED_SHA and PINNED_SHA in md and "UYARI" in md and "| D4_00_BASE|LONG |" in md
    assert "kod ağacı `t1`" in md and "temiz" in md
    T = B.read_trades(out / "book_lab_trades.csv.gz")
    assert set(T["book"]) == {"D4", "C4", "FM"} and {"real", "placebo", "labplacebo"} <= set(T["kind"])
    assert (T["period"].isin(["IS", "OOS"])).all() and not list(out.glob("*.part"))
    # işlem dosyasının özeti sıkıştırılmamış metnindir (gzip başlığından bağımsız)
    import gzip
    assert rep["trades_sha256"] == hashlib.sha256(gzip.decompress((out / "book_lab_trades.csv.gz").read_bytes())).hexdigest()
    assert all(u.startswith(L.ARCHIVE_BASE) and "/daily/fundingRate/" not in u for u in asked)
    # kayıtlı işlemlerden yalnız rapor: aynı hücre sonuçları (belirlenimci); filtre tablosu yoksa FİLTRE BEKLİYOR
    n_asked = len(asked)
    rep2 = B.run(cache_dir=cache, out_dir=out, filters_path=flt, report_only=True, log=lambda s: None, now_ms=now, code_state=CODE)
    assert len(asked) == n_asked
    strip = lambda r: {k: {kk: vv for kk, vv in v.items() if kk != "info"} for k, v in r["cells"].items()}  # noqa: E731
    assert strip(rep2) == strip(rep)
    # başka kod durumunda birleştirme / yalnız rapor YOK (açık izinle olur ve rapora yazılır); kirli ağaç da reddedilir
    import shutil
    cc = tmp_path / "out_code"
    shutil.copytree(out, cc)
    other = {**CODE, "code_tree": "t2", "commit": "beef"}
    with pytest.raises(B.BookDataError, match="kod durumunda"):
        B.run(cache_dir=cache, out_dir=cc, report_only=True, log=lambda s: None, now_ms=now, code_state=other)
    with pytest.raises(B.BookDataError, match="kod durumunda"):
        B.run(cache_dir=cache, out_dir=cc, report_only=True, log=lambda s: None, now_ms=now, code_state={**CODE, "dirty": True})
    with pytest.raises(B.BookDataError, match="kod durumunda"):
        B.run(cache_dir=cache, out_dir=cc, books=["D4"], offline=True, log=lambda s: None, now_ms=now, code_state=other,
              symbols_override=["BTC/USDT", "SYN/USDT"])
    ro = B.run(cache_dir=cache, out_dir=cc, report_only=True, log=lambda s: None, now_ms=now, code_state=other, allow_code_change=True)
    assert ro["code_overrides"][0]["before"] == CODE and ro["code_overrides"][0]["now"] == other
    assert "açık izinle" in (cc / "book_lab_report.md").read_text(encoding="utf-8")
    rep3 = B.run(cache_dir=cache, out_dir=out, report_only=True, log=lambda s: None, now_ms=now, code_state=CODE)
    assert all(cc_["capacity"] == {"status": B.ST_FILTER_WAIT} for cc_ in rep3["cells"].values())
    assert any(cc_["final"] == B.ST_FILTER_WAIT or "1:" in " ".join(cc_["target"]["failed"]) for k, cc_ in rep3["cells"].items()
               if k in B.PRIMARY_CELLS)
    # çevrimdışı tekrar: önbellekten, aynı işlemler; yalnız D4 yeniden üretilir, diğer defterler korunur
    rep4 = B.run(cache_dir=cache, out_dir=tmp_path / "out2", books=["D4"], filters_path=flt, offline=True, now_ms=now,
                 log=lambda s: None, symbols_override=["BTC/USDT", "SYN/USDT"], code_state=CODE)
    assert len(asked) == n_asked and rep4["complete"] is False and set(rep4["books"]) == {"D4"}
    T4 = B.read_trades(tmp_path / "out2" / "book_lab_trades.csv.gz")
    a = T[T["book"] == "D4"].sort_values(["variant", "kind", "symbol", "t_ms", "side"]).reset_index(drop=True)
    b = T4.sort_values(["variant", "kind", "symbol", "t_ms", "side"]).reset_index(drop=True)
    assert a[["variant", "kind", "symbol", "t_ms", "r", "funding_r"]].equals(b[["variant", "kind", "symbol", "t_ms", "r", "funding_r"]])
    # farklı mühürlü önceki koşu ezilmez
    meta = json.loads((out / "book_lab_meta.json").read_text(encoding="utf-8"))
    assert meta["code"] == CODE and meta["trades_sha256_basis"]
    meta["registry_sha"] = "0" * 16
    (tmp_path / "out3").mkdir()
    (tmp_path / "out3" / "book_lab_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(B.BookDataError, match="mühür"):
        B.run(cache_dir=cache, out_dir=tmp_path / "out3", offline=True, log=lambda s: None, now_ms=now, code_state=CODE)
    # PIT kipi: sahte liste, yalnız D4 (hız); PIT sonucu birincil rapora yazılır
    lst = '<ListBucketResult><IsTruncated>false</IsTruncated>' + "".join(
        f"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/{s}/</Prefix></CommonPrefixes>"
        for s in ("BTCUSDT", "SYNUSDT", "TSLAUSDT")) + "</ListBucketResult>"
    rp = B.run(cache_dir=cache, out_dir=out, universe="pit", books=["D4"], fetch=fetch, list_text=lambda u: lst, now_ms=now,
               log=lambda s: None, code_state=CODE)
    assert rp["universe"] == "pit" and rp["pit"]["excluded"] == {"TSLAUSDT": "hisse_etf_halka_arz_oncesi"}
    assert set(rp["pit"]["selected"]) == {"BTC/USDT", "SYN/USDT"} and rp["pit"]["missing_pairs"]["ok"] is True
    assert rp["pit"]["missing_pairs"]["pairs"] > 0
    assert all("pit_check" in cc_ and "NO_BUMP" in cc_["capacity"] and "BUMP" not in cc_["capacity"] for cc_ in rp["cells"].values())
    assert (out / "book_lab_report_pit.json").exists() and (cache / "pit_listing.json").exists()
    rep5 = B.run(cache_dir=cache, out_dir=out, filters_path=flt, report_only=True, log=lambda s: None, now_ms=now, code_state=CODE)
    assert "PIT teyidi" in (out / "book_lab_report.md").read_text(encoding="utf-8") and rep5["conclusion_tr"]
    assert "pit_not_applied" not in rep5


def test_pit_run_with_only_c4_still_checks_missing_pairs_from_1d(tmp_path, monkeypatch, mini):
    """Yalnız C4 (1d kullanmayan defter) PIT kipinde de eksik çift denetimi 1d serisiyle yapılır (her hücre PIT DOĞRULANAMADI
    olmaz); sembol görevleri burada boş (yalnız veri ve evren aşaması sınanır)."""
    _no_network(monkeypatch)
    files = fake_archive(_mini_frames(), ["BTC/USDT", "SYN/USDT"])
    seen = []
    monkeypatch.setattr(B, "_symbol_task", lambda args: (seen.append((args[0], args[2] is not None)), ([], {"symbol": args[0]}))[1])
    lst = '<ListBucketResult><IsTruncated>false</IsTruncated>' + "".join(
        f"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/{s}/</Prefix></CommonPrefixes>"
        for s in ("BTCUSDT", "SYNUSDT")) + "</ListBucketResult>"
    rp = B.run(cache_dir=tmp_path / "c", out_dir=tmp_path / "o", universe="pit", books=["C4"], fetch=files.get,
               list_text=lambda u: lst, now_ms=ms("2022-08-01"), log=lambda s: None, code_state=CODE)
    mp_ = rp["pit"]["missing_pairs"]
    assert mp_["pairs"] > 0 and mp_["missing"] == 0 and mp_["ok"] is True
    assert all(c["pit_check"]["status"] != B.ST_PIT_UNVERIFIED for c in rp["cells"].values())
    # görevlere 1d yalnız defter kullanıyorsa gider (C4 sonuçları 1d'den bağımsız)
    assert sorted(seen) == [("BTC/USDT", False), ("SYN/USDT", False)]


def test_data_errors_stop_the_run_before_any_output(tmp_path, monkeypatch, mini):
    """İndirme hatası, PRICE_END'den önce biten seri (birincil/bilgi) ve PRICE_END sonrası fonlama yokluğu → BookDataError;
    eksik evrenle rapor YAZILMAZ. PIT kipinde erken biten seri (DELISTED) hata değildir."""
    _no_network(monkeypatch)
    frames = _mini_frames()
    now = ms("2022-08-01")
    syms = ["BTC/USDT", "SYN/USDT"]
    monkeypatch.setattr(B, "_symbol_task", lambda args: ([], {"symbol": args[0]}))

    def go(name, files, **kw):
        out = tmp_path / name
        with pytest.raises(B.BookDataError) as ei:
            B.run(cache_dir=tmp_path / f"c_{name}", out_dir=out, books=["D4"], fetch=files.get if isinstance(files, dict) else files,
                  now_ms=now, log=lambda s: None, symbols_override=syms, code_state=CODE, **kw)
        assert not list(out.glob("book_lab_*"))                                       # hiçbir çıktı yok
        return str(ei.value)

    files = fake_archive(frames, syms)

    def flaky(url):                                                                   # tek sembolde ağ hatası (art arda değil)
        if "/SYNUSDT/4h/" in url and url.endswith("2022-05.zip"):
            raise ConnectionError("arşiv indirilemedi: test")
        return files.get(url)
    msg = go("net", flaky)
    assert "SYN/USDT" in msg and "ConnectionError" in msg
    short = dict(frames)
    short[("SYN/USDT", "4h")] = frames[("SYN/USDT", "4h")].iloc[:-30].reset_index(drop=True)
    assert "PRICE_END'den önce" in go("early", fake_archive(short, syms))
    short1 = dict(frames)
    short1[("SYN/USDT", "1d")] = frames[("SYN/USDT", "1d")].iloc[:-3].reset_index(drop=True)
    assert "1d serisi PRICE_END'e ulaşmıyor" in go("early1", fake_archive(short1, syms))
    msg = go("nofund", fake_archive(frames, syms, last_funding_month="2022-06"))
    assert "fonlama" in msg and "2022-07" in msg
    # PIT: erken biten seri hata değil (DELISTED); PRICE_END'e ulaşan sembolde fonlama yine şart
    lst = '<ListBucketResult><IsTruncated>false</IsTruncated>' + "".join(
        f"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/{s}/</Prefix></CommonPrefixes>"
        for s in ("BTCUSDT", "SYNUSDT")) + "</ListBucketResult>"
    early_all = dict(frames)
    early_all[("SYN/USDT", "4h")] = frames[("SYN/USDT", "4h")].iloc[:-30].reset_index(drop=True)
    early_all[("SYN/USDT", "1d")] = frames[("SYN/USDT", "1d")].iloc[:-5].reset_index(drop=True)
    rp = B.run(cache_dir=tmp_path / "c_pit", out_dir=tmp_path / "pit", universe="pit", books=["D4"],
               fetch=fake_archive(early_all, ["BTC/USDT"]).get, list_text=lambda u: lst, now_ms=now, log=lambda s: None,
               code_state=CODE)
    assert rp["universe"] == "pit" and rp["data_quality"]["SYN/USDT 4h"]["ends_at_price_end"] is False


def test_apply_pit_promotes_only_waiting_cells_and_only_from_the_same_code():
    def fresh():
        return {"universe": "primary", "code": CODE, "cells": {
            "D4_01_BTC_UP|LONG": {"final": B.ST_PIT_WAIT}, "D4_02_COIN_UP|LONG": {"final": B.ST_PIT_WAIT},
            "D4_00_BASE|LONG": {"final": B.ST_BASE}}}
    pit = {"registry_sha": B.BOOK_REGISTRY_SHA, "code": CODE, "cells": {
        "D4_01_BTC_UP|LONG": {"pit_check": {"pass": True, "status": B.ST_PIT_PASS}},
        "D4_02_COIN_UP|LONG": {"pit_check": {"pass": False, "status": B.ST_PIT_FAIL}},
        "D4_00_BASE|LONG": {"pit_check": {"pass": True, "status": B.ST_PIT_PASS}}}}
    rep = fresh()
    B.apply_pit(rep, pit)
    assert [rep["cells"][k]["final"] for k in ("D4_01_BTC_UP|LONG", "D4_02_COIN_UP|LONG", "D4_00_BASE|LONG")] == \
        [B.ST_CANDIDATE, B.ST_PIT_FAIL, B.ST_BASE]
    # §8.4: aday hangi defterin kaç varyantından biri
    assert rep["conclusion_tr"].startswith("ÖNERİ ADAYI: D4_01_BTC_UP|LONG (D4 defterinin 13 varyantından biri")
    assert B.pit_check({"capacity": {}, "stats": {}}, verified=False)["status"] == B.ST_PIT_UNVERIFIED
    # PIT raporu başka kod ağacından ya da kirli ağaçtan → yazılmaz, hücre beklemede kalır
    for code in ({**CODE, "code_tree": "t2"}, {**CODE, "dirty": True}, None):
        rep = fresh()
        B.apply_pit(rep, {**pit, "code": code})
        assert rep["cells"]["D4_01_BTC_UP|LONG"]["final"] == B.ST_PIT_WAIT and "kod" in rep["pit_not_applied"]
    assert not B.code_match(CODE, {**CODE, "dirty": None}) and B.code_match(CODE, dict(CODE))


def test_cli_offline_without_cache_fails_cleanly_and_logs_the_attempt(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("book_lab_cli", ROOT / "scripts" / "book_lab.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    out = tmp_path / "out"
    code = cli.main(["--cache", str(tmp_path / "c"), "--out", str(out), "--offline", "--jobs", "1", "--books", "D4"])
    assert code == 2
    lines = [json.loads(x) for x in (out / "book_lab_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["status"] for x in lines] == ["STARTED", "ERROR"] and lines[0]["attempt"] == lines[1]["attempt"]
    assert lines[1]["registry_sha"] == PINNED_SHA and "çevrimdışı" in lines[1]["error"]
    assert {"commit", "code_tree", "dirty"} <= set(lines[0]) and lines[0]["code_tree"]           # kod durumu kayıtta
    assert PINNED_SHA in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--universe", "yok", "--cache", "c", "--out", "o"])
    with pytest.raises(SystemExit):                                                   # --cache / --out zorunlu (depo içine yazılmaz)
        cli.main(["--offline"])
    st = B.git_state(ROOT)
    assert st["commit"] and len(st["code_tree"]) == 16 and isinstance(st["dirty"], bool)


def test_import_makes_no_network_call():
    src = (ROOT / "tradingbot" / "book_lab.py").read_text(encoding="utf-8")
    assert "urlopen(" not in src and "requests." not in src
